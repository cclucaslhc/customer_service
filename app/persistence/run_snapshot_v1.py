"""Immutable 0011 DDL for lifetime event counters and terminal snapshots."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_run_snapshots(metadata):
    runs = metadata.tables['agent_runs']
    runs.append_column(sa.Column('event_data_bytes',sa.BigInteger(),nullable=False,server_default='0'))
    runs.append_column(sa.Column('event_floor',sa.BigInteger(),nullable=False,server_default='0'))
    runs.append_constraint(sa.CheckConstraint('event_data_bytes >= 0 AND event_floor >= 0 AND event_floor <= event_sequence',name='run_event_accounting'))
    sa.Index('ix_agent_runs_event_retention',runs.c.finished_at,runs.c.id,
        postgresql_where=sa.text("execution_mode = 'queued' AND status IN ('complete','error','interrupted') AND event_floor < event_sequence"))
    sa.Table('chat_run_snapshots',metadata,
        sa.Column('tenant_id',sa.Uuid(),primary_key=True),sa.Column('run_id',sa.Uuid(),primary_key=True),
        sa.Column('sequence',sa.BigInteger(),nullable=False),sa.Column('state',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','run_id'],['agent_runs.tenant_id','agent_runs.id'],ondelete='CASCADE'),
        sa.CheckConstraint('sequence > 0',name='snapshot_sequence'),
        sa.CheckConstraint("octet_length(state::text) <= 4194304",name='snapshot_size'))
