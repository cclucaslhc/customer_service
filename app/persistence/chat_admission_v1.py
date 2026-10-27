"""Immutable 0012 admission schema builder."""
import sqlalchemy as sa


def add_chat_admission(metadata):
    runs = metadata.tables['agent_runs']
    runs.append_column(sa.Column('admission_started_at',sa.DateTime(timezone=True)))
    runs.append_column(sa.Column('admission_until',sa.DateTime(timezone=True)))
    sa.Index('ix_agent_runs_admission_until',runs.c.admission_until,
        postgresql_where=sa.text('admission_until IS NOT NULL'))
    sa.Index('ix_agent_runs_admission_started',runs.c.admission_started_at,
        postgresql_where=sa.text('admission_started_at IS NOT NULL'))
    sa.Table('chat_admission_policy',metadata,
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('namespace',sa.Uuid(),nullable=False),
        sa.Column('capacity',sa.Integer(),nullable=False),
        sa.Column('starts_per_minute',sa.Integer(),nullable=False),
        sa.CheckConstraint('id = 1 AND capacity BETWEEN 1 AND 1000 AND starts_per_minute BETWEEN 1 AND 60000',name='chat_admission_policy_bounds'))
