"""Immutable 0013 typed import queue and per-file publication ledger."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_knowledge_queue(meta):
    j = meta.tables['knowledge_jobs']
    j.append_constraint(sa.UniqueConstraint('tenant_id','id',name='uq_knowledge_jobs_tenant_id'))
    for column in (sa.Column('execution_mode',sa.Text(),nullable=False,server_default='local'),
        sa.Column('lease_owner',sa.Uuid()),sa.Column('lease_until',sa.DateTime(timezone=True)),
        sa.Column('lease_fence',sa.BigInteger(),nullable=False,server_default='0')):
        j.append_column(column)
    j.append_constraint(sa.CheckConstraint("execution_mode IN ('local','queued') AND lease_fence >= 0 AND ((lease_owner IS NULL AND lease_until IS NULL AND lease_fence=0) OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL AND lease_fence>0))",name='knowledge_job_lease'))
    sa.Index('ix_knowledge_jobs_queued',j.c.created_at,j.c.id,
        postgresql_where=sa.text("execution_mode='queued' AND status IN ('pending','running')"))
    sa.Table('knowledge_job_queue',meta,
        sa.Column('job_id',sa.Uuid(),primary_key=True),sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),nullable=False),sa.Column('session_hash',sa.String(64),nullable=False),
        sa.Column('deadline',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','job_id'],['knowledge_jobs.tenant_id','knowledge_jobs.id'],ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id','session_hash'],
            ['identity_sessions.tenant_id','identity_sessions.user_id','identity_sessions.token_hash'],ondelete='RESTRICT'))
    sa.Table('knowledge_job_items',meta,
        sa.Column('job_id',sa.Uuid(),primary_key=True),sa.Column('position',sa.Integer(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),sa.Column('relative_path',sa.Text(),nullable=False),
        sa.Column('sha256',sa.String(64)),sa.Column('size',sa.BigInteger(),nullable=False),
        sa.Column('status',sa.Text(),nullable=False,server_default='pending'),
        sa.Column('result',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False,server_default='{}'),
        sa.CheckConstraint("position BETWEEN 0 AND 499 AND size >= 0 AND status IN ('pending','imported','skipped','failed')",name='knowledge_item_bounds'),
        sa.ForeignKeyConstraint(['tenant_id','job_id'],['knowledge_jobs.tenant_id','knowledge_jobs.id'],ondelete='CASCADE'))
