"""Shared knowledge execution capacity; abandoned grants stay reserved."""
import sqlalchemy as sa

KNOWLEDGE_ADMISSION_LOCK = 1129533512


def add_knowledge_admission(meta):
    jobs = meta.tables['knowledge_jobs']
    jobs.append_column(sa.Column('execution_slot_held', sa.Boolean(), nullable=False,
                                server_default=sa.false()))
    jobs.append_constraint(sa.CheckConstraint(
        'NOT execution_slot_held OR (execution_mode=\'queued\' AND lease_owner IS NOT NULL AND lease_fence>0)',
        name='knowledge_execution_slot_owner'))
    sa.Index('ix_knowledge_jobs_held_slots', jobs.c.tenant_id,
             postgresql_where=sa.text('execution_slot_held'))
    return sa.Table('knowledge_admission_policy', meta,
        sa.Column('id', sa.SmallInteger(), primary_key=True),
        sa.Column('capacity', sa.Integer(), nullable=False),
        sa.Column('tenant_capacity', sa.Integer(), nullable=False),
        sa.CheckConstraint('id=1 AND capacity BETWEEN 1 AND 32 AND tenant_capacity BETWEEN 1 AND capacity',
                           name='knowledge_admission_bounds'))
