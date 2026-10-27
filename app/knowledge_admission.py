"""Migration-owner operations for shared knowledge execution capacity."""
import uuid
import sqlalchemy as sa
from app.persistence.schema import metadata
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.persistence.knowledge_admission_v1 import KNOWLEDGE_ADMISSION_LOCK
from app.recovery import require_operational


class KnowledgeAdmissionAdmin:
    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Knowledge capacity requires PostgreSQL')
        self.engine = engine

    @staticmethod
    def _guard(db):
        require_operational(db)
        if not db.execute(sa.text("SELECT pg_get_userbyid(relowner)=current_user FROM pg_class "
                                  "WHERE oid='public.knowledge_admission_policy'::regclass")).scalar_one():
            raise PermissionError('Migration owner required')
        db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key': KNOWLEDGE_ADMISSION_LOCK})

    @staticmethod
    def _audit(db, action, entity_id, details):
        db.execute(metadata.tables['audit_logs'].insert().values(id=uuid.uuid4(),
            tenant_id=uuid.UUID(LEGACY_TENANT_ID), actor_id=None,
            actor=db.exec_driver_sql('SELECT current_user').scalar_one(), action=action,
            entity_type='knowledge_capacity', entity_id=str(entity_id),
            summary='Explicit database-owner knowledge capacity operation',
            metadata_json=details, created_at=sa.func.clock_timestamp()))

    def configure(self, *, capacity, tenant_capacity):
        if (type(capacity) is not int or type(tenant_capacity) is not int
                or not 1 <= tenant_capacity <= capacity <= 32):
            raise ValueError('Invalid shared knowledge capacity')
        with self.engine.begin() as db:
            self._guard(db)
            jobs = metadata.tables['knowledge_jobs']
            if db.execute(sa.select(sa.exists(sa.select(jobs.c.id).where(
                    jobs.c.execution_slot_held.is_(True))))).scalar_one():
                raise ValueError('Drain and reconcile all held knowledge reservations first')
            changed = db.execute(metadata.tables['knowledge_admission_policy'].update().where(
                metadata.tables['knowledge_admission_policy'].c.id == 1).values(
                    capacity=capacity, tenant_capacity=tenant_capacity)).rowcount
            if changed != 1:
                raise ValueError('Shared knowledge capacity policy is missing')
            self._audit(db, 'knowledge.capacity.configure', 1,
                        dict(capacity=capacity, tenant_capacity=tenant_capacity))

    def snapshot(self):
        from app.persistence.base import wire
        with self.engine.begin() as db:
            self._guard(db)
            policy = db.execute(sa.select(metadata.tables['knowledge_admission_policy'])).mappings().one()
            jobs = metadata.tables['knowledge_jobs']
            rows = db.execute(sa.select(jobs.c.id,jobs.c.tenant_id,jobs.c.lease_owner,
                jobs.c.lease_fence,jobs.c.status,jobs.c.stage,jobs.c.lease_until).where(
                    jobs.c.execution_slot_held.is_(True)).order_by(jobs.c.id).limit(33)).mappings().all()
            return {'capacity': policy['capacity'], 'tenant_capacity': policy['tenant_capacity'],
                    'held': [wire(row) for row in rows]}

    def release_abandoned(self, *, job_id, owner, fence, parser_stopped_confirmed=False):
        if parser_stopped_confirmed is not True:
            raise ValueError('Confirm the old parser process group has stopped')
        if type(fence) is not int or fence < 1:
            raise ValueError('Invalid knowledge lease fence')
        jobs = metadata.tables['knowledge_jobs']
        with self.engine.begin() as db:
            self._guard(db)
            row = db.execute(sa.select(jobs).where(jobs.c.id == uuid.UUID(str(job_id)))
                             .with_for_update()).mappings().first()
            if (not row or str(row['lease_owner']) != str(uuid.UUID(str(owner)))
                    or row['lease_fence'] != fence or row['status'] not in {'complete','partial','failed','cancelled'}):
                raise ValueError('Reservation owner/fence or terminal state changed')
            if not row['execution_slot_held']:
                return False
            db.execute(jobs.update().where(jobs.c.id == row['id']).values(execution_slot_held=False))
            self._audit(db, 'knowledge.capacity.release', job_id, dict(fence=fence))
            return True
