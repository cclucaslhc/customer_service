"""Lease-fenced knowledge imports; old local jobs are never replayed."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.identity_schema_v1 import metadata as identity
from app.persistence.knowledge_queue_v1 import add_knowledge_queue

revision = '0013_knowledge_queue'
down_revision = '0012_chat_admission'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    for table in (*baseline.tables.values(),*identity.tables.values()):
        table.to_metadata(meta)
    meta.tables['knowledge_jobs'].append_column(sa.Column('tenant_id',sa.Uuid()))
    add_knowledge_queue(meta)
    for column in ('execution_mode','lease_owner','lease_until','lease_fence'):
        op.add_column('knowledge_jobs',meta.tables['knowledge_jobs'].c[column]._copy())
    op.create_unique_constraint('uq_knowledge_jobs_tenant_id','knowledge_jobs',['tenant_id','id'])
    op.create_check_constraint('knowledge_job_lease','knowledge_jobs',
        "execution_mode IN ('local','queued') AND lease_fence >= 0 AND ((lease_owner IS NULL AND lease_until IS NULL AND lease_fence=0) OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL AND lease_fence>0))")
    op.create_index('ix_knowledge_jobs_queued','knowledge_jobs',['created_at','id'],
        postgresql_where=sa.text("execution_mode='queued' AND status IN ('pending','running')"))
    for name in ('knowledge_job_queue','knowledge_job_items'):
        meta.tables[name].create(op.get_bind(),checkfirst=False)


def downgrade():
    raise RuntimeError('Knowledge queue downgrade is disabled: publication/identity records must be preserved')
