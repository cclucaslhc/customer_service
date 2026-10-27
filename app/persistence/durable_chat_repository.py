"""Typed queue: one claim, no blind model/tool replay; PostgreSQL is the fact source."""
import uuid
from datetime import timedelta
import sqlalchemy as sa
from app.identity import AuthenticationError, AuthorizationError, DelegatedSession, token_hash
from app.persistence.base import wire
from app.persistence.chat_repository import ChatRepository
from app.persistence.identity_repository import IdentityRepository
from app.persistence.run_events import append, terminal
from app.run_leases import RunLease, RunLeaseLost


class DurableChatConflict(ValueError):
    pass


class EventCursorExpired(DurableChatConflict):
    pass


def validate_retention(seconds):
    if type(seconds) is not int or not 86400 <= seconds <= 90*86400:
        raise ValueError('Event retention must be explicitly set between 1 and 90 days')
    return seconds


class DurableChatRepository(ChatRepository):
    def __init__(self, engine, **kwargs):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Durable chat requires PostgreSQL')
        super().__init__(engine, **kwargs)

    def snapshot(self, session_id, run_id=None):
        """History and cursor/draft share the session lock; no completion gap.

        Snapshot reflects authorization now, not the submitter's stale role.
        Only whitelisted public fields leave this internal queue boundary.
        """
        s,r,e,m = (self.tables[n] for n in ('chat_sessions','agent_runs','chat_run_events','messages'))
        sid = self.key(s,session_id)
        with self.engine.begin() as db:
            self._lock_session(db,sid)
            session = db.execute(sa.select(s).where(s.c.id == sid,self._scope(db,s),s.c.deleted_at.is_(None))).mappings().first()
            if not session:
                return None
            query = sa.select(r).where(r.c.session_id == sid,r.c.execution_mode == 'queued')
            if run_id is not None:
                query = query.where(r.c.id == self.key(r,run_id))
            else:
                query = query.where(r.c.status.in_(('queued','running')))
            run = db.execute(query.order_by(r.c.started_at.desc(),r.c.id.desc()).limit(1)).mappings().first()
            if run_id is not None and run is None:
                return None
            messages = [wire(row) for row in db.execute(sa.select(m.c.id,m.c.role,m.c.content,m.c.status,m.c.provider,m.c.model,
                m.c.citations_json.label('citations'),m.c.feedback,m.c.created_at).where(m.c.session_id == sid,
                m.c.status == 'complete').order_by(m.c.sequence)).mappings()]
            state = None
            if run:
                state = {'run_id':str(run['id']),'status':run['status'],'cursor':run['event_sequence'],
                    'event_floor':run['event_floor'],
                    'cancel_requested':run['cancel_requested_at'] is not None,'draft':'','citations':[],
                    'error_code':run['error_code'],'message_id':None,'contact_summary':None}
                if run['status'] not in ('queued','running'):
                    snapshots = self.tables['chat_run_snapshots']
                    stored = db.execute(sa.select(snapshots).where(snapshots.c.run_id == run['id'],
                        snapshots.c.tenant_id == run['tenant_id'])).mappings().first()
                    if not stored or stored['sequence'] != run['event_sequence']:
                        raise DurableChatConflict('Terminal snapshot is unavailable or inconsistent')
                    for key in ('draft','citations','message_id','contact_summary'):
                        state[key] = stored['state'][key]
                    return {'session':wire(session),'messages':messages,'run':state}
                rows = db.execute(sa.select(e.c.event,e.c.data).where(e.c.run_id == run['id'],
                    e.c.tenant_id == run['tenant_id']).order_by(e.c.sequence)).mappings()
                for item in rows:
                    event,data = item['event'],item['data']
                    if event == 'answer_reset':
                        state['draft'] = ''; state['citations'] = []; state['contact_summary'] = None
                    elif event == 'token':
                        state['draft'] += str(data.get('text',''))
                    elif event == 'citation':
                        state['citations'].append(data)
                    elif event == 'contact_form':
                        state['contact_summary'] = data.get('summary')
                    elif event == 'done':
                        state['message_id'] = data.get('message_id')
            return {'session':wire(session),'messages':messages,'run':state}

    def enqueue(self, *, session_id, request_id, content, title, provider, model, prompt_version_id, instructions, trace_context=None):
        from app.tracing import validate_carrier
        trace_context = validate_carrier(trace_context)
        s,r,q,m = (self.tables[n] for n in ('chat_sessions','agent_runs','chat_run_queue','messages'))
        sid,rid = self.key(s,session_id),self.key(r,request_id)
        with self.engine.begin() as db:
            principal = self._principal(db)
            if principal is None:
                raise AuthorizationError('Queue submission requires an authenticated actor')
            self._lock_session(db, sid)
            existing = db.execute(sa.select(r).where(r.c.id == rid).with_for_update()).mappings().first()
            if existing:
                prior = db.execute(sa.select(q).where(q.c.run_id == rid)).mappings().first()
                if not prior or existing['session_id'] != sid or not self._allowed(db,sid) or str(prior['actor_id']) != principal.user_id:
                    raise DurableChatConflict('Request identifier unavailable')
                original = db.execute(sa.select(m.c.content).where(m.c.id == prior['user_message_id'])).scalar_one()
                if original != content:
                    raise DurableChatConflict('Request identifier conflicts with content')
                return str(rid)
            session = db.execute(sa.select(s).where(s.c.id == sid).with_for_update()).mappings().first()
            if session and (not self._allowed(db,sid) or session['deleted_at'] or session['archived_at']):
                raise DurableChatConflict('Session unavailable')
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            tenant = uuid.UUID(principal.tenant_id)
            if session is None:
                db.execute(s.insert().values(id=sid,tenant_id=tenant,owner_id=uuid.UUID(principal.user_id),
                    title=title,status='idle',created_at=now,updated_at=now))
            old = db.execute(sa.select(r).where(r.c.session_id == sid,r.c.status.in_(('queued','running')))
                .with_for_update()).mappings().all()
            for row in old:
                db.execute(r.update().where(r.c.id == row['id']).values(status='interrupted',error_code='superseded',
                    cancel_requested_at=now,finished_at=now,
                    lease_fence=row['lease_fence'] + (1 if row['lease_owner'] is not None else 0)))
                t = self.tables['agent_run_tools']
                db.execute(t.update().where(t.c.run_id == row['id'],t.c.finished_at.is_(None)).values(status='interrupted',finished_at=now))
                terminal(db,row,'interrupted','superseded')
            message_id = str(uuid.uuid4())
            self._insert_message(db,id=message_id,session_id=session_id,role='user',content=content,created_at=now)
            db.execute(r.insert().values(**self.values(r,id=request_id,session_id=session_id,tenant_id=principal.tenant_id,
                status='queued',execution_mode='queued',provider=provider,model=model,prompt_version_id=prompt_version_id,started_at=now)))
            db.execute(q.insert().values(run_id=rid,tenant_id=tenant,actor_id=uuid.UUID(principal.user_id),
                session_hash=token_hash(self._token),user_message_id=uuid.UUID(message_id),instructions=instructions,
                created_at=now,deadline=now+timedelta(seconds=120),trace_context=trace_context))
            run = db.execute(sa.select(r).where(r.c.id == rid)).mappings().one()
            append(db,run,'run_started',{'run_id':str(rid)})
            append(db,run,'run_queued',{'run_id':str(rid)})
            db.execute(s.update().where(s.c.id == sid).values(status='running',updated_at=now))
            return str(rid)

    def read_events(self, session_id, run_id, *, after=0, limit=100):
        if type(after) is not int or after < 0 or not 1 <= limit <= 100:
            raise ValueError('Invalid event cursor')
        r,e = self.tables['agent_runs'],self.tables['chat_run_events']
        with self.engine.begin() as db:
            self._lock_session(db,session_id)
            if not self._allowed(db,session_id):
                return None
            sessions = self.tables['chat_sessions']
            if db.execute(sa.select(sessions.c.id).where(sessions.c.id == self.key(sessions,session_id),
                sessions.c.deleted_at.is_(None))).first() is None:
                return None
            run = db.execute(sa.select(r).where(r.c.id == self.key(r,run_id),r.c.session_id == self.key(r,session_id),
                r.c.execution_mode == 'queued')).mappings().first()
            if not run:
                return None
            if after > run['event_sequence']:
                raise DurableChatConflict('Event cursor is ahead of this Run')
            if after < run['event_floor']:
                raise EventCursorExpired('Event cursor expired; read the authorized Run snapshot')
            events = [wire(row) for row in db.execute(sa.select(e).where(e.c.run_id == run['id'],
                e.c.tenant_id == run['tenant_id'],e.c.sequence > after).order_by(e.c.sequence).limit(limit)).mappings()]
            return {'status':run['status'],'sequence':run['event_sequence'],'events':events}

    def prune_events(self, *, retention_seconds, limit=100):
        """Trusted maintenance only. Preserve immutable terminal state and counters.

        No default retention policy; operators must approve and enable one.
        Run/session lock order matches writers/subscribers; skip busy targets.
        """
        if self._token is not None or self._lease is not None:
            raise AuthorizationError('Event cleanup is an internal operation')
        validate_retention(retention_seconds)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Event cleanup batch must be between 1 and 100 Runs')
        r,e,p = (self.tables[n] for n in ('agent_runs','chat_run_events','chat_run_snapshots'))
        cutoff = sa.func.clock_timestamp()-sa.literal(timedelta(seconds=retention_seconds),type_=sa.Interval())
        eligible = sa.and_(r.c.execution_mode == 'queued',r.c.status.in_(('complete','error','interrupted')),
            r.c.finished_at <= cutoff,r.c.event_floor < r.c.event_sequence)
        joined = r.join(p,sa.and_(p.c.run_id == r.c.id,p.c.tenant_id == r.c.tenant_id,p.c.sequence == r.c.event_sequence))
        with self.engine.connect() as db:
            candidates = db.execute(sa.select(r.c.id,r.c.session_id).select_from(joined).where(eligible)
                .order_by(r.c.finished_at,r.c.id).limit(limit)).all()
        count,removed = 0,0
        for rid,sid in candidates:
            with self.engine.begin() as db:
                if not db.execute(sa.select(sa.func.pg_try_advisory_xact_lock(self._session_lock_key(sid)))).scalar_one():
                    continue
                run = db.execute(sa.select(r).select_from(joined).where(r.c.id == rid,eligible)
                    .with_for_update(of=r,skip_locked=True)).mappings().first()
                if not run:
                    continue
                removed += db.execute(e.delete().where(e.c.run_id == rid,e.c.tenant_id == run['tenant_id'])).rowcount
                db.execute(r.update().where(r.c.id == rid).values(event_floor=run['event_sequence']))
                count += 1
        return {'runs':count,'events':removed}

    def append_event(self, run_id, event, data):
        if event not in {'provider','token','answer_reset','tool_start','tool_end'}:
            raise ValueError('Unsupported transient Run event')
        r = self.tables['agent_runs']
        with self.engine.begin() as db:
            self._principal(db)
            rid = self.key(r,run_id)
            sid = db.execute(sa.select(r.c.session_id).where(r.c.id == rid)).scalar_one()
            self._lock_session(db,sid)
            row = db.execute(sa.select(r).where(r.c.id == rid).with_for_update()).mappings().one()
            if not self._allowed(db,sid) or row['status'] != 'running' or not self._lease_current(db,row):
                raise RunLeaseLost()
            if row['event_sequence'] >= 10000:
                raise ValueError('Run event count limit exceeded')
            return append(db,row,event,data)

    def _close_pending(self, db, row, code):
        r,s = self.tables['agent_runs'],self.tables['chat_sessions']
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        db.execute(r.update().where(r.c.id == row['id']).values(status='interrupted',error_code=code,finished_at=now))
        terminal(db,row,'interrupted',code)
        db.execute(s.update().where(s.c.id == row['session_id']).values(status=sa.case(
            (sa.exists(sa.select(r.c.id).where(r.c.session_id == row['session_id'],r.c.status.in_(('queued','running')))), 'running'),else_='idle'),updated_at=now))

    def has_pending(self):
        if self._token is not None or self._lease is not None:
            raise AuthorizationError('Queue inspection is an internal Worker operation')
        r = self.tables['agent_runs']
        with self.engine.connect() as db:
            return db.execute(sa.select(sa.exists(sa.select(r.c.id).where(
                r.c.status == 'queued',r.c.execution_mode == 'queued')))).scalar_one()

    def claim_next(self, *, admission_policy=None):
        from app.recovery import check_operational
        check_operational(self.engine)
        if self._token is not None or self._lease is not None:
            raise AuthorizationError('Queue claim is an internal Worker operation')
        r,q,s,m = (self.tables[n] for n in ('agent_runs','chat_run_queue','chat_sessions','messages'))
        with self.engine.connect() as db:
            candidates = db.execute(sa.select(r.c.id,r.c.session_id).join(q,q.c.run_id == r.c.id)
                .where(r.c.status == 'queued',r.c.execution_mode == 'queued').order_by(q.c.created_at,q.c.run_id).limit(100)).all()
        for rid,sid in candidates:
            with self.engine.begin() as db:
                from app.persistence.admission_repository import lock_admission, read_policy
                from app.admission import CoordinationUnavailable
                lock_admission(db)
                configured = read_policy(db)
                if configured != admission_policy:
                    raise CoordinationUnavailable('Worker must match the durable shared admission policy')
                if configured is not None:
                    # Survives Redis eviction/flush/failover. This is not a
                    # fallback mode: a Worker must still hold a Redis permit.
                    running = db.execute(sa.select(sa.func.count()).select_from(r).where(sa.or_(
                        r.c.admission_until > sa.func.clock_timestamp(),
                        sa.and_(r.c.status == 'running',sa.or_(r.c.lease_until.is_(None),
                            r.c.lease_until > sa.func.clock_timestamp()))))).scalar_one()
                    starts = db.execute(sa.select(sa.func.count()).select_from(r).where(
                        r.c.admission_started_at > sa.func.clock_timestamp()-sa.text("interval '60 seconds'"))).scalar_one()
                    if running >= configured.capacity or starts >= configured.starts_per_minute:
                        return None
                if not db.execute(sa.select(sa.func.pg_try_advisory_xact_lock(self._session_lock_key(sid)))).scalar_one():
                    continue
                row = db.execute(sa.select(r).where(r.c.id == rid,r.c.status == 'queued')
                    .with_for_update(skip_locked=True)).mappings().first()
                if not row:
                    continue
                item = db.execute(sa.select(q).where(q.c.run_id == rid)).mappings().one()
                session = db.execute(sa.select(s).where(s.c.id == sid)).mappings().one()
                now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                reason = 'cancelled' if row['cancel_requested_at'] else 'queue_timeout' if item['deadline'] <= now else 'session_inactive' if session['archived_at'] or session['deleted_at'] else None
                credential = DelegatedSession(item['session_hash'])
                try:
                    principal = IdentityRepository(self.engine).resolve(credential,connection=db)
                    principal.require_resource(str(row['tenant_id']),str(session['owner_id']),'chat')
                    if principal.user_id != str(item['actor_id']):
                        raise AuthorizationError('Queue actor changed')
                except (AuthenticationError,AuthorizationError):
                    reason = 'identity_revoked'
                if reason:
                    self._close_pending(db,row,reason)
                    continue
                message = db.execute(sa.select(m).where(m.c.id == item['user_message_id'],m.c.session_id == sid,
                    m.c.tenant_id == row['tenant_id'],m.c.role == 'user')).mappings().one()
                owner = uuid.uuid4()
                fence = row['lease_fence'] + 1
                db.execute(r.update().where(r.c.id == rid).values(status='running',lease_owner=owner,
                    lease_until=now+timedelta(seconds=30),lease_fence=fence,
                    admission_started_at=now if configured else None,
                    admission_until=now+timedelta(seconds=30) if configured else None))
                return {'lease':RunLease(str(rid),str(owner),fence),'credential':credential,
                    'run_id':str(rid),'session_id':str(sid),'content':message['content'],
                    'instructions':item['instructions'],'provider':row['provider'],'model':row['model'],
                    'prompt_version_id':str(row['prompt_version_id']) if row['prompt_version_id'] else None,
                    'user_sequence':message['sequence'],'trace_context':item['trace_context']}
        return None

    def release_admission(self, job):
        """Only after generator/client teardown; never infer release from terminal status.

        A preempted Run retains its reservation until the old owner acknowledges
        exit or DB-clock expiry. Owner equality suffices even after fence changes:
        Run IDs are never reassigned or replayed to another execution owner.
        """
        if self._token is not None or self._lease is not None:
            raise AuthorizationError('Admission release is an internal Worker operation')
        r = self.tables['agent_runs']
        with self.engine.begin() as db:
            from app.persistence.admission_repository import lock_admission
            lock_admission(db)
            self._lock_session(db,job['session_id'])
            return bool(db.execute(r.update().where(r.c.id == uuid.UUID(job['run_id']),
                r.c.lease_owner == uuid.UUID(job['lease'].owner),r.c.status.in_(('complete','error','interrupted')))
                .values(admission_until=None)).rowcount)

    def reap_pending(self):
        if self._token is not None or self._lease is not None:
            raise AuthorizationError('Pending cleanup is an internal operation')
        r,q = self.tables['agent_runs'],self.tables['chat_run_queue']
        with self.engine.connect() as db:
            candidates = db.execute(sa.select(r.c.id,r.c.session_id).join(q,q.c.run_id == r.c.id).where(
                r.c.status == 'queued',sa.or_(r.c.cancel_requested_at.is_not(None),q.c.deadline <= sa.func.clock_timestamp()))
                .order_by(q.c.created_at).limit(100)).all()
        count = 0
        for rid,sid in candidates:
            with self.engine.begin() as db:
                if not db.execute(sa.select(sa.func.pg_try_advisory_xact_lock(self._session_lock_key(sid)))).scalar_one():
                    continue
                row = db.execute(sa.select(r).join(q,q.c.run_id == r.c.id).where(r.c.id == rid,r.c.status == 'queued',
                    sa.or_(r.c.cancel_requested_at.is_not(None),q.c.deadline <= sa.func.clock_timestamp()))
                    .with_for_update(of=r,skip_locked=True)).mappings().first()
                if row:
                    self._close_pending(db,row,'cancelled' if row['cancel_requested_at'] else 'queue_timeout')
                    count += 1
        return count
