"""Authenticated one-claim imports; publish and item progress commit together."""
from dataclasses import dataclass, replace
from datetime import timedelta
import json
import uuid
import sqlalchemy as sa
from app.identity import AuthenticationError, AuthorizationError, DelegatedSession, token_hash
from app.persistence.identity_repository import IdentityRepository
from app.persistence.knowledge_repository import KnowledgeRepository


class KnowledgeLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class KnowledgeLease:
    job_id: str
    owner: str
    fence: int
    position: int | None = None


class DurableKnowledgeRepository(KnowledgeRepository):
    def __init__(self,engine,*,token=None,lease=None):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Knowledge queue requires PostgreSQL')
        super().__init__(engine,token=token)
        self.lease = lease

    def with_lease(self,lease):
        return type(self)(self.engine,token=self._token,lease=lease)

    def with_item(self,position):
        if self.lease is None or type(position) is not int or not 0 <= position < 500:
            raise ValueError('Invalid publication item')
        return self.with_lease(replace(self.lease,position=position))

    def _internal(self):
        if self._token is not None or self.lease is not None:
            raise AuthorizationError('Internal knowledge maintenance only')

    def enqueue(self,job_id,source,recursive,manifest,*,trace_context=None):
        from app.tracing import validate_carrier
        trace_context = validate_carrier(trace_context)
        if len(manifest) > 500 or [row['position'] for row in manifest] != list(range(len(manifest))):
            raise ValueError('Invalid import manifest')
        j,q,i = (self.tables[n] for n in ('knowledge_jobs','knowledge_job_queue','knowledge_job_items'))
        with self.engine.begin() as db:
            from app.recovery import require_operational
            require_operational(db)
            p = self._principal(db,'knowledge.manage')
            if p is None:
                raise AuthorizationError('Authenticated import actor required')
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            jid,tenant = uuid.UUID(job_id),uuid.UUID(p.tenant_id)
            from app.object_store import ObjectReference
            for item in manifest:
                display = item.get('display_name')
                if display is not None and (not isinstance(display,str) or not 1<=len(display)<=255
                                              or '/' in display or '\\' in display):
                    raise ValueError('Invalid immutable display name')
                target,version = item.get('replace_document_id'),item.get('replace_document_version')
                if target is not None or version is not None:
                    if target is None or type(version) is not int or version < 1:
                        raise ValueError('Invalid reparse target')
                    documents = self.tables['documents']
                    current = db.execute(sa.select(documents.c.id).where(
                        documents.c.id == uuid.UUID(str(target)),documents.c.tenant_id == tenant,
                        documents.c.version == version).with_for_update()).first()
                    if current is None:
                        raise ValueError('Reparse target changed or unavailable')
                if item.get('input_object') is not None:
                    reference = ObjectReference.from_dict(item['input_object'])
                    if (reference.size != item['size'] or reference.sha256 != item['sha256']
                            or not reference.key.startswith(f'tenants/{tenant}/knowledge/')
                            or not isinstance(reference.version_id,str) or reference.version_id in {'','null'}):
                        raise ValueError('Input reference disagrees with tenant manifest')
            db.execute(j.insert().values(id=jid,tenant_id=tenant,requested_by=uuid.UUID(p.user_id),
                source_path=source,recursive=recursive,status='pending',stage='pending',execution_mode='queued',
                total_count=len(manifest),created_at=now))
            db.execute(q.insert().values(job_id=jid,tenant_id=tenant,actor_id=uuid.UUID(p.user_id),
                session_hash=token_hash(self._token),deadline=now+timedelta(hours=1),trace_context=trace_context))
            if manifest:
                db.execute(i.insert(),[{**row,'job_id':jid,'tenant_id':tenant,'result':{}} for row in manifest])
            db.execute(self.tables['audit_logs'].insert().values(id=uuid.uuid4(),tenant_id=tenant,
                actor_id=uuid.UUID(p.user_id),actor=p.user_id,action='knowledge.job.enqueue',
                entity_type='knowledge_job',entity_id=str(jid),summary='创建后台文档处理任务',
                metadata_json={'item_count':len(manifest),'operation':
                    'reparse' if any(row.get('replace_document_id') for row in manifest)
                    else 'upload' if any(row.get('display_name') for row in manifest) else 'path'},created_at=now))

    def claim_next(self):
        self._internal()
        from app.recovery import check_operational
        check_operational(self.engine)
        j,q = (self.tables[n] for n in ('knowledge_jobs','knowledge_job_queue'))
        with self.engine.begin() as db:
            db.execute(sa.text("SET LOCAL cs.knowledge_action_version='1'"))
            from app.persistence.knowledge_admission_v1 import KNOWLEDGE_ADMISSION_LOCK
            db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key': KNOWLEDGE_ADMISSION_LOCK})
            policy = db.execute(sa.select(self.tables['knowledge_admission_policy'])).mappings().one()
            held = dict(db.execute(sa.select(j.c.tenant_id,sa.func.count()).where(
                j.c.execution_slot_held.is_(True)).group_by(j.c.tenant_id)).all())
            if sum(held.values()) >= policy['capacity']:
                return None
            full = [tenant for tenant,count in held.items() if count >= policy['tenant_capacity']]
            rows = db.execute(sa.select(j).join(q,q.c.job_id == j.c.id).where(j.c.execution_mode == 'queued',j.c.status == 'pending',
                j.c.tenant_id.not_in(full))
                .order_by(j.c.created_at,j.c.id).limit(100).with_for_update(of=j,skip_locked=True)).mappings().all()
            for row in rows:
                item = db.execute(sa.select(q).where(q.c.job_id == row['id'])).mappings().one()
                now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                credential = DelegatedSession(item['session_hash'])
                reason = 'queue_timeout' if item['deadline'] <= now else None
                try:
                    p = IdentityRepository(self.engine).resolve(credential,connection=db)
                    p.require('knowledge.manage')
                    if p.tenant_id != str(row['tenant_id']) or p.user_id != str(row['requested_by']):
                        raise AuthorizationError('Import identity changed')
                except (AuthenticationError,AuthorizationError):
                    reason = 'identity_revoked'
                if reason:
                    db.execute(j.update().where(j.c.id == row['id']).values(status='failed',stage=reason,
                        error_message='任务授权或排队期限已失效，请重新提交',finished_at=now))
                    continue
                owner = str(uuid.uuid4())
                fence = row['lease_fence']+1
                db.execute(j.update().where(j.c.id == row['id']).values(status='running',stage='scanning',
                    started_at=now,lease_owner=uuid.UUID(owner),lease_until=now+timedelta(seconds=60),lease_fence=fence,
                    execution_slot_held=True))
                return {'lease':KnowledgeLease(str(row['id']),owner,fence),'credential':credential,
                        'trace_context':item['trace_context']}
        return None

    def release_slot(self, lease):
        """Worker acknowledgement after parser shutdown; valid even after expiry.

        Expiry maintenance never releases this reservation. A dead Worker needs
        explicit operator confirmation, so an unknown parser cannot overlap a
        replacement just because its database lease expired.
        """
        self._internal()
        j = self.tables['knowledge_jobs']
        with self.engine.begin() as db:
            from app.recovery import require_operational
            require_operational(db)
            return bool(db.execute(j.update().where(j.c.id == uuid.UUID(lease.job_id),
                j.c.lease_owner == uuid.UUID(lease.owner), j.c.lease_fence == lease.fence,
                j.c.execution_slot_held.is_(True)).values(execution_slot_held=False)).rowcount)

    def cancel(self, job_id):
        """Revoke publication atomically; only the stopped Worker releases capacity."""
        if self.lease is not None or self._token is None:
            raise AuthorizationError('Authenticated administrator required')
        j = self.tables['knowledge_jobs']
        with self.engine.begin() as db:
            from app.recovery import require_operational
            require_operational(db)
            p = self._principal(db, 'knowledge.manage')
            if p is None:
                raise AuthorizationError('Authenticated administrator required')
            row = db.execute(sa.select(j).where(j.c.id == uuid.UUID(str(job_id)),
                j.c.tenant_id == uuid.UUID(p.tenant_id)).with_for_update()).mappings().first()
            if row is None or row['execution_mode'] != 'queued':
                raise ValueError('Queued knowledge job unavailable')
            if row['status'] not in ('pending', 'running'):
                return False
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            db.execute(j.update().where(j.c.id == row['id']).values(status='cancelled',stage='cancelled',
                current_file=None,finished_at=now,
                error_message='任务已取消；已发布内容保留，后台解析停止后释放执行容量'))
            db.execute(self.tables['audit_logs'].insert().values(id=uuid.uuid4(),
                tenant_id=uuid.UUID(p.tenant_id),actor_id=uuid.UUID(p.user_id),actor=p.user_id,
                action='knowledge.job.cancel',entity_type='knowledge_job',entity_id=str(row['id']),
                summary='取消后台文档处理任务',metadata_json={'previous_status':row['status']},created_at=now))
            return True

    def _guard(self,db):
        from app.recovery import require_operational
        require_operational(db)
        if self.lease is None:
            raise KnowledgeLeaseLost('A publication lease is required')
        p = self._principal(db,'knowledge.manage')
        j = self.tables['knowledge_jobs']
        row = db.execute(sa.select(j).where(j.c.id == uuid.UUID(self.lease.job_id)).with_for_update()).mappings().first()
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        q = self.tables['knowledge_job_queue']
        original_session = db.execute(sa.select(q.c.session_hash).where(q.c.job_id == uuid.UUID(self.lease.job_id))).scalar_one_or_none()
        if (p is None or not row or row['execution_mode'] != 'queued' or row['status'] != 'running'
            or not row['execution_slot_held']
            or row['tenant_id'] != uuid.UUID(p.tenant_id) or row['requested_by'] != uuid.UUID(p.user_id)
            or str(row['lease_owner']) != self.lease.owner or row['lease_fence'] != self.lease.fence
            or row['lease_until'] <= now or original_session != token_hash(self._token)):
            raise KnowledgeLeaseLost('Import execution authority was lost')
        return row

    def renew(self):
        with self.engine.begin() as db:
            row = self._guard(db)
            db.execute(self.tables['knowledge_jobs'].update().where(self.tables['knowledge_jobs'].c.id == row['id'])
                .values(lease_until=sa.func.clock_timestamp()+sa.text("interval '60 seconds'")))

    def require_manage(self):
        if self.lease is None:
            return super().require_manage()
        with self.engine.begin() as db:
            self._guard(db)

    def manifest(self):
        i = self.tables['knowledge_job_items']
        with self.engine.begin() as db:
            row = self._guard(db)
            return [dict(item) for item in db.execute(sa.select(i).where(i.c.job_id == row['id'],i.c.tenant_id == row['tenant_id'])
                .order_by(i.c.position)).mappings()]

    def progress(self,values):
        allowed = {'stage','current_file'}
        with self.engine.begin() as db:
            row = self._guard(db)
            db.execute(self.tables['knowledge_jobs'].update().where(self.tables['knowledge_jobs'].c.id == row['id'])
                .values(**{key:value for key,value in values.items() if key in allowed}))

    def _sync(self,db,row):
        i,j = self.tables['knowledge_job_items'],self.tables['knowledge_jobs']
        items = db.execute(sa.select(i).where(i.c.job_id == row['id']).order_by(i.c.position)).mappings().all()
        results = {key:[item['result'] for item in items if item['status']==status]
            for key,status in (('imported','imported'),('skipped','skipped'),('failed','failed'))}
        counts = {key+'_count':len(values) for key,values in results.items()}
        db.execute(j.update().where(j.c.id == row['id']).values(**counts,processed_count=sum(counts.values()),
            result_json={**results,**counts,'source_path':row['source_path']}))

    def _record(self,db,status,result):
        row = self._guard(db)
        i = self.tables['knowledge_job_items']
        if self.lease.position is None:
            raise KnowledgeLeaseLost('A publication item is required')
        count = db.execute(i.update().where(i.c.job_id == row['id'],i.c.tenant_id == row['tenant_id'],
            i.c.position == self.lease.position,i.c.status == 'pending').values(status=status,result=result)).rowcount
        if count != 1:
            raise KnowledgeLeaseLost('Import item was already committed or unavailable')
        self._sync(db,row)

    def record(self,status,result):
        if status not in ('skipped','failed'):
            raise ValueError('Only publication can mark an item imported')
        with self.engine.begin() as db:
            self._record(db,status,result)

    def _publication_guard(self,db,document):
        row = self._guard(db)
        i = self.tables['knowledge_job_items']
        item = db.execute(sa.select(i).where(i.c.job_id == row['id'],i.c.position == self.lease.position,
            i.c.tenant_id == row['tenant_id'],i.c.status == 'pending')).mappings().first()
        if not item or item['sha256'] != document['sha256'] or item['size'] != document['size']:
            raise KnowledgeLeaseLost('Publication does not match the fixed import manifest')
        if item['replace_document_id'] is not None and (
                str(item['replace_document_id']) != str(document['id'])
                or document['version'] != item['replace_document_version'] + 1):
            raise KnowledgeLeaseLost('Publication does not match pinned reparse target')

    def _publication_committed(self,db,document):
        meta = document.get('metadata_json') or {}
        warnings = document.get('warnings_json') or []
        if isinstance(meta,str):
            meta = json.loads(meta)
        if isinstance(warnings,str):
            warnings = json.loads(warnings)
        self._record(db,'imported',{'id':str(document['id']),'display_name':document['display_name'],
            'status':'ready','version':document['version'],'chunk_count':document['chunk_count'],
            'detected_type':document.get('detected_type'),'asset_count':meta.get('asset_count',0),'warnings':warnings})

    def finish(self):
        with self.engine.begin() as db:
            row = self._guard(db)
            i,j = self.tables['knowledge_job_items'],self.tables['knowledge_jobs']
            if db.execute(sa.select(sa.exists(sa.select(i.c.job_id).where(i.c.job_id == row['id'],i.c.status == 'pending')))).scalar_one():
                raise KnowledgeLeaseLost('Cannot finish an import with pending items')
            status = 'partial' if row['failed_count'] and (row['imported_count'] or row['skipped_count']) else 'failed' if row['failed_count'] else 'complete'
            db.execute(j.update().where(j.c.id == row['id']).values(status=status,stage=status,current_file=None,finished_at=sa.func.clock_timestamp()))
            return status

    def fail(self):
        with self.engine.begin() as db:
            row = self._guard(db)
            j = self.tables['knowledge_jobs']
            db.execute(j.update().where(j.c.id == row['id']).values(status='failed',stage='worker_failed',
                error_message='执行已中断；已发布内容保留，请重新提交',current_file=None,finished_at=sa.func.clock_timestamp()))

    def reap_expired(self):
        self._internal()
        j,q = self.tables['knowledge_jobs'],self.tables['knowledge_job_queue']
        with self.engine.begin() as db:
            rows = db.execute(sa.select(j.c.id,j.c.status).join(q,q.c.job_id==j.c.id).where(j.c.execution_mode=='queued',sa.or_(
                sa.and_(j.c.status=='running',j.c.lease_until <= sa.func.clock_timestamp()),
                sa.and_(j.c.status=='pending',q.c.deadline <= sa.func.clock_timestamp())))
                .order_by(j.c.created_at,j.c.id).limit(100).with_for_update(of=j,skip_locked=True)).all()
            for job_id,status in rows:
                stage = 'lease_expired' if status == 'running' else 'queue_timeout'
                db.execute(j.update().where(j.c.id == job_id).values(status='failed',stage=stage,
                    error_message='执行租约已过期；已发布内容保留，请核对后重新提交' if status == 'running'
                    else '排队期限已过期，请重新提交',current_file=None,finished_at=sa.func.clock_timestamp()))
            return len(rows)
