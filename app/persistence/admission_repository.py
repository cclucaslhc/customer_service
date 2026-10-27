"""Explicit immutable global policy, never auto-created by ordinary Workers."""
import uuid
import sqlalchemy as sa
from app.admission import AdmissionPolicy, CoordinationUnavailable
from app.persistence.schema import metadata

ADMISSION_LOCK = 648321984113700012


def lock_admission(db):
    db.execute(sa.select(sa.func.pg_advisory_xact_lock(ADMISSION_LOCK)))


def read_policy(db):
    row = db.execute(sa.select(metadata.tables['chat_admission_policy'])).mappings().first()
    return AdmissionPolicy(str(row['namespace']),row['capacity'],row['starts_per_minute']) if row else None


def configure_policy(engine, policy):
    if engine.dialect.name != 'postgresql':
        raise ValueError('Shared admission requires PostgreSQL')
    with engine.begin() as db:
        lock_admission(db)
        current = read_policy(db)
        if current == policy:
            return
        if current is not None:
            raise CoordinationUnavailable('Admission policy is immutable; use a reviewed maintenance migration')
        r = metadata.tables['agent_runs']
        if db.execute(sa.select(sa.exists(sa.select(r.c.id).where(r.c.status.in_(('queued','running')))))).scalar_one():
            raise CoordinationUnavailable('Drain existing Runs before configuring admission')
        db.execute(metadata.tables['chat_admission_policy'].insert().values(id=1,
            namespace=uuid.UUID(policy.namespace),capacity=policy.capacity,starts_per_minute=policy.starts_per_minute))
