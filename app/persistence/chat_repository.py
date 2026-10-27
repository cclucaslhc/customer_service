"""SQLAlchemy chat persistence shared by local SQLite and production PostgreSQL.

Each method commits a short transaction; callers never keep a connection while
waiting for model/tool network operations. Token-bound PostgreSQL repositories
enforce chat tenant/owner policy; other business boundaries remain in progress.
"""

import hashlib
import uuid
from datetime import timedelta

import sqlalchemy as sa
from app.persistence.base import LocalRepository, Repository, wire
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.identity import PERMISSIONS
from app.run_leases import RunLease, RunLeaseLost, RunCancellationRequested


class ChatRepository(Repository):
    table_names = ("chat_sessions", "messages", "agent_runs", "agent_run_tools", "contacts")

    def __init__(self, engine, *, token=None, lease=None):
        super().__init__(engine)
        if token is not None and engine.dialect.name != "postgresql":
            raise ValueError("Authenticated chat requires PostgreSQL")
        self._token = token
        self._lease = lease

    def with_lease(self, lease):
        if self.engine.dialect.name != 'postgresql' or not isinstance(lease, RunLease):
            raise ValueError('Run fencing requires PostgreSQL and an internal lease')
        return type(self)(self.engine, token=self._token, lease=lease)

    def model_meter(self, run_id, *, provider, model):
        from app.persistence.model_usage_repository import ModelUsageRepository
        return ModelUsageRepository(self,run_id,provider=provider,model=model)

    def _lease_current(self, db, row, *, allow_cancel=False):
        from app.recovery import require_operational
        require_operational(db)
        if 'lease_owner' not in row or row['lease_owner'] is None:
            return self._lease is None
        lease = self._lease
        if lease is None or str(row['id']) != lease.run_id or str(row['lease_owner']) != lease.owner or row['lease_fence'] != lease.fence:
            return False
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        return row['lease_until'] > now and (allow_cancel or row['cancel_requested_at'] is None)

    @staticmethod
    def _session_lock_key(session_id):
        # PostgreSQL UUID keys are case-insensitive; the advisory lock must be
        # canonical too (including reaper UUID objects vs HTTP string IDs).
        value = str(session_id)
        try:
            value = str(uuid.UUID(value))
        except ValueError:
            pass  # Legacy SQLite session IDs need not be UUIDs.
        return int.from_bytes(hashlib.sha256(f'chat-session:{value}'.encode()).digest()[:8], 'big') & ((1 << 63) - 1)

    def authenticated(self, token):
        """Bind a credential, not caller-provided role/owner claims; no shared mutation."""
        from app.persistence.identity_repository import IdentityRepository
        IdentityRepository(self.engine).resolve(token)
        return type(self)(self.engine, token=token)

    def _principal(self, db):
        if self._token is None:
            return None
        from app.persistence.identity_repository import IdentityRepository
        principal = IdentityRepository(self.engine).resolve(self._token, connection=db)
        principal.require("chat.own")
        return principal

    def _scope(self, db, sessions):
        principal = self._principal(db)
        if "tenant_id" not in sessions.c:
            return sa.true()
        if principal is None:
            # Transitional local integration path can only access quarantined
            # records; it is not an all-tenant service/administrator privilege.
            return sessions.c.tenant_id == self.values(sessions, tenant_id=LEGACY_TENANT_ID)["tenant_id"]
        scope = sessions.c.tenant_id == self.values(sessions, tenant_id=principal.tenant_id)["tenant_id"]
        if "chat.tenant" not in PERMISSIONS[principal.role]:
            scope = sa.and_(scope, sessions.c.owner_id == self.values(sessions, owner_id=principal.user_id)["owner_id"])
        return scope

    def _allowed(self, db, session_id):
        sessions = self.tables["chat_sessions"]
        return bool(db.execute(sa.select(sa.exists(sa.select(sessions.c.id).where(
            sessions.c.id == self.key(sessions, session_id), self._scope(db, sessions))))).scalar_one())

    def _tenant_fields(self, db, session_id):
        sessions = self.tables["chat_sessions"]
        if "tenant_id" not in sessions.c:
            return {}
        return {"tenant_id": db.execute(sa.select(sessions.c.tenant_id).where(sessions.c.id == self.key(sessions, session_id))).scalar_one()}

    def _lock_session(self, db, session_id):
        # Transaction-scoped coordination includes not-yet-existing sessions.
        # Stable across processes and restarts; never uses Python's salted hash.
        self.serialize_writes(db, self._session_lock_key(session_id))

    def _message_order(self, table):
        if "sequence" in table.c:
            return [table.c.sequence]
        return [table.c.created_at, sa.literal_column("messages.rowid")]

    def _insert_message(self, db, **fields):
        # Caller holds the session transaction lock before allocating an ordinal.
        table = self.tables["messages"]
        values = self.values(table, **fields)
        values.update(self._tenant_fields(db, fields["session_id"]))
        if "sequence" in table.c:
            values["sequence"] = db.execute(sa.select(sa.func.coalesce(sa.func.max(table.c.sequence), 0) + 1)
                .where(table.c.session_id == values["session_id"])).scalar_one()
        db.execute(table.insert().values(**values))

    def lifecycle(self, table, scope):
        return {
            "active": sa.and_(table.c.deleted_at.is_(None), table.c.archived_at.is_(None)),
            "archived": sa.and_(table.c.deleted_at.is_(None), table.c.archived_at.is_not(None)),
            "deleted": table.c.deleted_at.is_not(None), "all": sa.true(),
        }[scope]

    def session(self, session_id, include_deleted=False):
        sessions = self.tables["chat_sessions"]
        with self.engine.connect() as db:
            query = sa.select(sessions).where(sessions.c.id == self.key(sessions, session_id), self._scope(db, sessions))
            if not include_deleted:
                query = query.where(sessions.c.deleted_at.is_(None))
            row = wire(db.execute(query).mappings().first())
            if row:
                row["title"] = (row["title"] or "").strip() or "新会话"
            return row

    def can_start_session(self, session_id):
        """New IDs may be created, but existing hidden IDs may not be reused."""
        sessions = self.tables["chat_sessions"]
        with self.engine.connect() as db:
            scope = self._scope(db, sessions)
            key = self.key(sessions, session_id)
            exists = db.execute(sa.select(sessions.c.id).where(sessions.c.id == key)).first()
            return exists is None or bool(db.execute(sa.select(sessions.c.id).where(sessions.c.id == key, scope)).first())

    def list_sessions(self, limit=50, scope="active", admin=False):
        s, m, r, c = (self.tables[name] for name in ("chat_sessions", "messages", "agent_runs", "contacts"))
        message_count = sa.select(sa.func.count()).where(m.c.session_id == s.c.id, m.c.status == "complete").correlate(s).scalar_subquery()
        latest_message = sa.select(m.c.content).where(m.c.session_id == s.c.id, m.c.status == "complete").order_by(*(column.desc() for column in self._message_order(m))).limit(1).correlate(s).scalar_subquery()
        columns = [s, message_count.label("message_count"), latest_message.label("last_message")]
        if admin:
            def latest(column, *conditions):
                return sa.select(column).where(r.c.session_id == s.c.id, *conditions).order_by(r.c.started_at.desc(), r.c.id.desc()).limit(1).correlate(s).scalar_subquery()
            columns += [
                sa.select(sa.func.count()).where(r.c.session_id == s.c.id).correlate(s).scalar_subquery().label("run_count"),
                sa.select(sa.func.count()).where(r.c.session_id == s.c.id, r.c.status == "error").correlate(s).scalar_subquery().label("error_count"),
                latest(r.c.error_code, r.c.error_code.is_not(None)).label("last_error_code"),
                latest(r.c.provider).label("provider"), latest(r.c.model).label("model"),
                sa.select(sa.func.count()).where(c.c.session_id == s.c.id).correlate(s).scalar_subquery().label("contact_count"),
            ]
        query = sa.select(*columns).where(self.lifecycle(s, scope))
        if not admin:
            query = query.where(message_count > 0)
        query = query.order_by(s.c.updated_at.desc(), s.c.created_at.desc(), s.c.id).limit(limit)
        with self.engine.connect() as db:
            principal = self._principal(db)
            if admin and principal:
                principal.require("chat.tenant")
            records = [wire(row) for row in db.execute(query.where(self._scope(db, s))).mappings()]
        for row in records:
            row["title"] = (row["title"] or "").strip() or "新会话"
        return records

    def update_session(self, session_id, *, now, include_deleted=False, **fields):
        if set(fields) - {"title", "status", "management_status", "assignee", "tags_json", "archived_at", "deleted_at"}:
            raise ValueError("Unsupported session update")
        s = self.tables["chat_sessions"]
        query = s.update().where(s.c.id == self.key(s, session_id))
        if not include_deleted:
            query = query.where(s.c.deleted_at.is_(None))
        with self.engine.begin() as db:
            principal = self._principal(db)
            self._lock_session(db, session_id)
            if principal and set(fields) & {"management_status", "assignee", "tags_json"}:
                principal.require("chat.tenant")
            return bool(db.execute(query.where(self._scope(db, s)).values(**self.values(s, updated_at=now, **fields))).rowcount)

    def history(self, session_id):
        m, s = self.tables["messages"], self.tables["chat_sessions"]
        sid = self.values(m, session_id=session_id)["session_id"]
        with self.engine.connect() as db:
            visible = sa.exists(sa.select(s.c.id).where(s.c.id == m.c.session_id, self._scope(db, s)))
            return [wire(row) for row in db.execute(sa.select(m).where(m.c.session_id == sid, m.c.status == "complete", visible).order_by(*self._message_order(m))).mappings()]

    def feedback(self, session_id, message_id, rating):
        s, m = self.tables["chat_sessions"], self.tables["messages"]
        if rating not in (None, "up", "down"):
            raise ValueError("Invalid feedback")
        with self.engine.begin() as db:
            query = m.update().where(
                m.c.id == self.key(m, message_id), m.c.session_id == self.key(s, session_id), m.c.role == "assistant",
                sa.exists(sa.select(s.c.id).where(s.c.id == m.c.session_id, s.c.deleted_at.is_(None), self._scope(db, s))),
            ).values(feedback=rating)
            return bool(db.execute(query).rowcount)

    def start_run(self, *, session_id, run_id, user_id, title, content, provider, model, prompt_version_id, now,
                  lease_owner=None, lease_seconds=30):
        s, m, r = (self.tables[name] for name in ("chat_sessions", "messages", "agent_runs"))
        sid = self.key(s, session_id)
        if lease_owner is not None:
            if self.engine.dialect.name != 'postgresql' or type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
                raise ValueError('Invalid PostgreSQL Run lease')
            lease_owner = uuid.UUID(str(lease_owner))
        with self.engine.begin() as db:
            principal = self._principal(db)
            if self.engine.dialect.name == 'postgresql':
                from app.persistence.admission_repository import lock_admission, read_policy
                from app.admission import CoordinationUnavailable
                lock_admission(db)
                if read_policy(db) is not None:
                    raise CoordinationUnavailable('Shared admission requires the durable Worker path')
            self._lock_session(db, session_id)
            row = db.execute(sa.select(s).where(s.c.id == sid).with_for_update()).mappings().first()
            if row and not self._allowed(db, session_id):
                return False
            if row and (row["deleted_at"] or row["archived_at"]):
                return False
            if self.engine.dialect.name == 'postgresql' and db.execute(sa.select(r.c.id).where(
                r.c.session_id == sid,r.c.execution_mode == 'queued',r.c.status.in_(('queued','running'))).limit(1)).first():
                return False  # Do not mix direct executors with queued commands.
            if not row:
                ownership = {"tenant_id": principal.tenant_id, "owner_id": principal.user_id} if principal else {}
                db.execute(s.insert().values(**self.values(s, id=session_id, title=title, status="idle", created_at=now, updated_at=now, **ownership)))
            lease_fields = {}
            if lease_owner is not None:
                # Preemption and the new user message/Run commit atomically. The
                # prior process observes terminal state on its next lease check.
                previous = db.execute(sa.select(r.c.id).where(r.c.session_id == sid, r.c.status == 'running',
                    r.c.lease_owner.is_not(None)).with_for_update()).scalars().all()
                db_now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                if previous:
                    db.execute(r.update().where(r.c.id.in_(previous)).values(status='interrupted',
                        error_code='superseded', finished_at=db_now, cancel_requested_at=db_now,
                        lease_fence=r.c.lease_fence + 1))
                    tools = self.tables['agent_run_tools']
                    db.execute(tools.update().where(tools.c.run_id.in_(previous), tools.c.finished_at.is_(None))
                        .values(status='interrupted', finished_at=db_now))
                lease_fields = {'lease_owner': lease_owner, 'lease_fence': 1,
                                'lease_until': db_now + timedelta(seconds=lease_seconds)}
            self._insert_message(db, id=user_id, session_id=session_id, role="user", content=content, created_at=now)
            db.execute(r.insert().values(**self.values(r, id=run_id, session_id=session_id, status="running", provider=provider, model=model, prompt_version_id=prompt_version_id, started_at=now), **self._tenant_fields(db, session_id), **lease_fields))
            fields = {"status": "running", "updated_at": now}
            if not row or not (row["title"] or "").strip():
                fields["title"] = title
            db.execute(s.update().where(s.c.id == sid).values(**self.values(s, **fields)))
            return True

    def _finish(self, db, run_id, status, error_code, now, *, session_locked=False):
        self._principal(db)
        s, r, t = (self.tables[name] for name in ("chat_sessions", "agent_runs", "agent_run_tools"))
        rid = self.key(r, run_id)
        if not session_locked:
            if self.engine.dialect.name == "sqlite":
                self.serialize_writes(db, 0)
            else:
                sid = db.execute(sa.select(r.c.session_id).where(r.c.id == rid)).scalar_one_or_none()
                if sid is None:
                    return False
                self._lock_session(db, sid)
        row = db.execute(sa.select(r).where(r.c.id == rid).with_for_update()).mappings().first()
        if not row or row["status"] != "running" or not self._allowed(db, row["session_id"]):
            return False
        if not self._lease_current(db, row, allow_cancel=status == 'interrupted'):
            return False
        db.execute(r.update().where(r.c.id == rid, r.c.status == "running").values(**self.values(r, status=status, error_code=error_code, finished_at=now)))
        if status != "complete":
            db.execute(t.update().where(t.c.run_id == rid, t.c.finished_at.is_(None)).values(**self.values(t, status=status, finished_at=now)))
        db.execute(s.update().where(s.c.id == row["session_id"]).values(
            status=sa.case((sa.exists(sa.select(r.c.id).where(r.c.session_id == row["session_id"], r.c.status.in_(("running","queued")))), "running"), else_="idle"),
            **self.values(s, updated_at=now),
        ))
        if status != 'complete' and row.get('execution_mode') == 'queued':
            from app.persistence.run_events import terminal
            terminal(db,row,status,error_code)
        return True

    def finish_run(self, run_id, status, error_code, now):
        if status not in {"complete", "error", "interrupted"}:
            raise ValueError("Invalid terminal run status")
        with self.engine.begin() as db:
            return self._finish(db, run_id, status, error_code, now)

    def complete_run(self, *, run_id, session_id, message_id, content, provider, model, citations, now,
                     contact_summary=None, evidence_sources=None, index_pinned=False, index_id=None):
        r, m, s = (self.tables[name] for name in ("agent_runs", "messages", "chat_sessions"))
        with self.engine.begin() as db:
            self._principal(db)
            self._lock_session(db, session_id)
            if not self._allowed(db, session_id):
                return False
            row = db.execute(sa.select(r).where(r.c.id == self.key(r, run_id)).with_for_update()).mappings().first()
            if not row or row["status"] != "running" or row["session_id"] != self.values(m, session_id=session_id)["session_id"]:
                return False
            if not self._lease_current(db, row):
                return False
            session = db.execute(sa.select(s).where(s.c.id == row["session_id"])).mappings().one()
            if session["archived_at"] or session["deleted_at"]:
                self._finish(db, run_id, "interrupted", "session_inactive", now, session_locked=True)
                return False
            if (index_pinned or row.get('rag_route') is not None) and self.engine.dialect.name == 'postgresql':
                from app.knowledge_retrieval import KnowledgeIndexChanged
                from app.rag_index_admin import RAG_PUBLICATION_LOCK
                # Hold a shared publication lock through commit. Activation uses
                # the exclusive counterpart, so no switch can fit between this
                # check and the assistant message insert/transaction commit.
                db.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),
                           {'key': RAG_PUBLICATION_LOCK})
                if row.get('rag_route') is not None:
                    from app.rag_routing import current_route,context_from_route
                    route=current_route(db,credential=self._token,tenant_id=session['tenant_id'],session_id=session_id,
                        lock_identities=True,runtime_context=context_from_route(row['rag_route']))
                    if route!=row['rag_route'] or (index_pinned and route['index_id']!=(str(index_id) if index_id else None)):
                        raise KnowledgeIndexChanged()
                else:
                    active = db.execute(sa.text('''SELECT index_id FROM knowledge_index_versions
                        WHERE tenant_id=:tenant AND status='active' '''),
                        {'tenant': session['tenant_id']}).scalar_one_or_none()
                    if active != (uuid.UUID(str(index_id)) if index_id is not None else None):
                        raise KnowledgeIndexChanged()
            if evidence_sources and self.engine.dialect.name == 'postgresql':
                import hashlib
                from app.knowledge_retrieval import KnowledgeEvidenceChanged
                chunks = self.tables['knowledge_chunks']
                documents = self.tables['documents']
                for source in sorted(evidence_sources, key=lambda item: str(item['id'])):
                    current = db.execute(sa.select(chunks.c.content, documents.c.version).join(
                        documents, sa.and_(documents.c.id == chunks.c.document_id,
                            documents.c.tenant_id == chunks.c.tenant_id)).where(
                        chunks.c.id == source['id'], documents.c.tenant_id == session['tenant_id'],
                        documents.c.status == 'ready',
                        documents.c.version == source['document_version']).with_for_update(read=True,
                            of=[chunks, documents])).first()
                    if not current or hashlib.sha256(current.content.encode('utf-8')).hexdigest() != source['content_sha256']:
                        raise KnowledgeEvidenceChanged()
            # Recheck lease before writing either result. If the final check
            # expires, no assistant row can accidentally commit on False.
            if row.get('rag_route') is not None:
                if current_route(db,credential=self._token,tenant_id=session['tenant_id'],
                        session_id=session_id,lock_identities=True,
                        runtime_context=context_from_route(row['rag_route']))!=row['rag_route']:
                    raise KnowledgeIndexChanged()
            if not self._finish(db, run_id, "complete", None, now, session_locked=True):
                return False
            self._insert_message(db, id=message_id, session_id=session_id, role="assistant", content=content, provider=provider, model=model, citations_json=citations, created_at=now)
            if row.get('execution_mode') == 'queued':
                from app.persistence.run_events import terminal
                terminal(db,row,'complete',message_id=message_id,citations=citations,contact_summary=contact_summary)
            return True

    def pin_rag_route(self,*,run_id,session_id,runtime_context=None):
        """Pin exactly once after authorized start/claim, before any retrieval.

        The PostgreSQL vector orchestrator calls this before model execution.
        Do not use a returned route to skip scoped retrieval or submit fencing.
        """
        if self.engine.dialect.name!='postgresql':
            raise ValueError('Durable RAG routing requires PostgreSQL')
        from app.rag_routing import current_route
        from app.knowledge_retrieval import KnowledgeIndexChanged
        from app.rag_index_admin import RAG_PUBLICATION_LOCK
        runs,sessions=self.tables['agent_runs'],self.tables['chat_sessions']
        with self.engine.begin() as db:
            self._principal(db)
            self._lock_session(db,session_id)
            if not self._allowed(db,session_id):
                raise PermissionError('Session unavailable')
            row=db.execute(sa.select(runs).where(runs.c.id==self.key(runs,run_id),
                runs.c.session_id==self.key(sessions,session_id)).with_for_update()).mappings().first()
            if not row or row['status']!='running' or not self._lease_current(db,row):
                raise RunLeaseLost()
            session=db.execute(sa.select(sessions).where(sessions.c.id==row['session_id'])).mappings().one()
            if session['archived_at'] or session['deleted_at']:
                raise PermissionError('Session unavailable')
            db.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':RAG_PUBLICATION_LOCK})
            route=current_route(db,credential=self._token,tenant_id=session['tenant_id'],session_id=session_id,
                runtime_context=runtime_context)
            if row['rag_route'] is not None:
                if row['rag_route']!=route:
                    raise KnowledgeIndexChanged()
                return route
            db.execute(runs.update().where(runs.c.id==row['id']).values(rag_route=route))
            return route

    def start_tool(self, run_id, tool_name, now):
        t, r = self.tables["agent_run_tools"], self.tables["agent_runs"]
        with self.engine.begin() as db:
            self._principal(db)
            session_id = db.execute(sa.select(r.c.session_id).where(r.c.id == self.key(r, run_id))).scalar_one_or_none()
            if session_id is None or not self._allowed(db, session_id):
                return None
            self._lock_session(db, session_id)
            run = db.execute(sa.select(r).where(r.c.id == self.key(r, run_id)).with_for_update()).mappings().one()
            if run['status'] != 'running' or not self._lease_current(db, run):
                raise RunLeaseLost()
            result = db.execute(t.insert().values(**self.values(t, run_id=run_id, tool_name=tool_name, status="running", started_at=now), **self._tenant_fields(db, session_id)))
            return result.inserted_primary_key[0]

    def finish_tool(self, tool_id, now):
        t, r, s = (self.tables[name] for name in ("agent_run_tools", "agent_runs", "chat_sessions"))
        with self.engine.begin() as db:
            self._principal(db)
            run_id = db.execute(sa.select(t.c.run_id).where(t.c.id == tool_id)).scalar_one_or_none()
            if run_id is None:
                return False
            sid = db.execute(sa.select(r.c.session_id).where(r.c.id == run_id)).scalar_one_or_none()
            if sid is None or not self._allowed(db, sid):
                return False
            self._lock_session(db, sid)
            run = db.execute(sa.select(r).where(r.c.id == run_id).with_for_update()).mappings().one()
            if run['status'] != 'running' or not self._lease_current(db, run):
                return False
            parent = sa.exists(sa.select(r.c.id).join(s, s.c.id == r.c.session_id).where(r.c.id == t.c.run_id, self._scope(db, s)))
            return bool(db.execute(t.update().where(t.c.id == tool_id, t.c.finished_at.is_(None), parent).values(**self.values(t, status="complete", finished_at=now))).rowcount)

    def request_cancel(self, session_id, run_id=None):
        """Authorization and the cancel intent commit before any local notification."""
        if self.engine.dialect.name != 'postgresql':
            return False
        s, r = self.tables['chat_sessions'], self.tables['agent_runs']
        with self.engine.begin() as db:
            self._principal(db)
            self._lock_session(db, session_id)
            if not self._allowed(db, session_id):
                return False
            query = r.update().where(r.c.session_id == self.key(s, session_id),
                r.c.status.in_(('running','queued')),sa.or_(r.c.lease_owner.is_not(None),r.c.execution_mode == 'queued'))
            if run_id is not None:
                query = query.where(r.c.id == self.key(r, run_id))
            return bool(db.execute(query.values(cancel_requested_at=sa.func.coalesce(
                r.c.cancel_requested_at, sa.func.clock_timestamp()))).rowcount)

    def renew_lease(self, *, lease_seconds=30, raise_on_cancel=False):
        """No expired owner can renew; database time is read after acquiring locks."""
        if self._lease is None or type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise ValueError('Invalid Run lease renewal')
        r = self.tables['agent_runs']
        with self.engine.begin() as db:
            self._principal(db)
            rid = self.key(r, self._lease.run_id)
            sid = db.execute(sa.select(r.c.session_id).where(r.c.id == rid)).scalar_one_or_none()
            if sid is None or not self._allowed(db, sid):
                return False
            self._lock_session(db, sid)
            row = db.execute(sa.select(r).where(r.c.id == rid).with_for_update()).mappings().one()
            if row['status'] != 'running' or not self._lease_current(db, row, allow_cancel=True):
                return False
            session = db.execute(sa.select(self.tables['chat_sessions']).where(
                self.tables['chat_sessions'].c.id == sid)).mappings().one()
            if session['archived_at'] or session['deleted_at']:
                return False
            # Distinguish only while the same owner/fence is still unexpired.
            # Cancellation never renews authority, even in the boolean API.
            if row['cancel_requested_at'] is not None:
                if raise_on_cancel:
                    raise RunCancellationRequested()
                return False
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            renewal = {'lease_until':now + timedelta(seconds=lease_seconds)}
            if row['admission_started_at'] is not None:
                renewal['admission_until'] = renewal['lease_until']
            db.execute(r.update().where(r.c.id == rid).values(**renewal))
            return True

    def reap_expired_leases(self, *, limit=100):
        """Terminalize expired Runs, except one provably pre-dispatch queue retry.

        Not an HTTP/customer API. Each short transaction takes session lock first,
        then SKIP LOCKED Run row, avoiding inversion with normal Run writers.
        A model attempt is committed before provider I/O under this same lock.
        Absence of that record plus no tool/output evidence is the only retry
        authority; unknown external effects are never replayed.
        """
        if self.engine.dialect.name != 'postgresql' or self._token is not None or self._lease is not None:
            raise ValueError('Lease cleanup requires an explicit internal PostgreSQL repository')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid lease cleanup batch')
        r, s, t = (self.tables[name] for name in ('agent_runs', 'chat_sessions', 'agent_run_tools'))
        q, e, attempts = (self.tables[name] for name in ('chat_run_queue','chat_run_events','model_usage_attempts'))
        expiry = sa.and_(r.c.status == 'running', r.c.lease_owner.is_not(None), r.c.lease_until <= sa.func.clock_timestamp())
        with self.engine.connect() as db:
            candidates = db.execute(sa.select(r.c.id, r.c.session_id).where(expiry)
                .order_by(r.c.lease_until, r.c.id).limit(limit)).all()
        count = 0
        for rid, sid in candidates:
            with self.engine.begin() as db:
                if not db.execute(sa.select(sa.func.pg_try_advisory_xact_lock(self._session_lock_key(sid)))).scalar_one():
                    continue
                row = db.execute(sa.select(r).where(r.c.id == rid, expiry)
                    .with_for_update(skip_locked=True)).mappings().first()
                if row is None:
                    continue
                now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                # The first claim has fence=1. Requeue fences it to 2 and the
                # second claim uses 3, so even a second pre-dispatch crash is
                # terminal. Never infer retry safety from lease expiry alone.
                if row['execution_mode'] == 'queued' and row['lease_fence'] == 1 and row['cancel_requested_at'] is None and row['event_sequence'] <= 3:
                    session = db.execute(sa.select(s.c.archived_at,s.c.deleted_at).where(s.c.id == sid)).one()
                    deadline = db.execute(sa.select(q.c.deadline).where(q.c.run_id == rid)).scalar_one_or_none()
                    no_effect_evidence = not db.execute(sa.select(sa.exists(sa.select(attempts.c.attempt_id).where(
                        attempts.c.run_id == rid)))).scalar_one()
                    no_effect_evidence &= not db.execute(sa.select(sa.exists(sa.select(t.c.id).where(
                        t.c.run_id == rid)))).scalar_one()
                    no_effect_evidence &= not db.execute(sa.select(sa.exists(sa.select(e.c.run_id).where(
                        e.c.run_id == rid,e.c.event.notin_(('run_started','run_queued','provider')))))).scalar_one()
                    if not any(session) and deadline is not None and deadline > now and no_effect_evidence:
                        db.execute(r.update().where(r.c.id == rid).values(status='queued',
                            lease_fence=r.c.lease_fence + 1))
                        from app.persistence.run_events import append
                        append(db,row,'run_queued',{'run_id':str(rid),'reason':'pre_dispatch_lease_expired'})
                        count += 1
                        continue
                db.execute(r.update().where(r.c.id == rid).values(status='interrupted',
                    error_code='lease_expired', finished_at=now, lease_fence=r.c.lease_fence + 1))
                db.execute(t.update().where(t.c.run_id == rid, t.c.finished_at.is_(None))
                    .values(status='interrupted', finished_at=now))
                run = db.execute(sa.select(r).where(r.c.id == rid)).mappings().one()
                if run.get('execution_mode') == 'queued':
                    from app.persistence.run_events import terminal
                    terminal(db,run,'interrupted','lease_expired')
                db.execute(s.update().where(s.c.id == sid).values(updated_at=now, status=sa.case(
                    (sa.exists(sa.select(r.c.id).where(r.c.session_id == sid, r.c.status.in_(('running','queued')))), 'running'), else_='idle')))
                count += 1
        return count


class LocalChatRepository(LocalRepository):
    repository_type = ChatRepository
