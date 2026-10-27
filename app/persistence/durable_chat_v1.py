"""Immutable 0010 definitions: typed queue and private ordered Run events."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_durable_chat(metadata):
    runs = metadata.tables['agent_runs']
    runs.append_column(sa.Column('execution_mode', sa.Text(), nullable=False, server_default='direct'))
    runs.append_column(sa.Column('event_sequence', sa.BigInteger(), nullable=False, server_default='0'))
    runs.append_constraint(sa.CheckConstraint("execution_mode IN ('direct','queued') AND event_sequence >= 0", name='run_execution_mode'))
    sa.Index('uq_agent_runs_queued_active_session', runs.c.session_id, unique=True,
        postgresql_where=sa.text("execution_mode = 'queued' AND status IN ('queued','running')"))
    messages = metadata.tables['messages']
    messages.append_constraint(sa.UniqueConstraint('tenant_id', 'id', name='uq_messages_tenant_id'))
    sessions = metadata.tables['identity_sessions']
    sessions.append_constraint(sa.UniqueConstraint('tenant_id', 'user_id', 'token_hash', name='uq_identity_sessions_scope_hash'))
    queue = sa.Table('chat_run_queue', metadata,
        sa.Column('run_id', sa.Uuid(), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=False),
        sa.Column('session_hash', sa.String(64), nullable=False),
        sa.Column('user_message_id', sa.Uuid(), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','run_id'], ['agent_runs.tenant_id','agent_runs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id','user_message_id'], ['messages.tenant_id','messages.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id','session_hash'],
            ['identity_sessions.tenant_id','identity_sessions.user_id','identity_sessions.token_hash'], ondelete='RESTRICT'),
        sa.CheckConstraint('deadline > created_at', name='queue_deadline'))
    sa.Index('ix_chat_run_queue_created', queue.c.created_at, queue.c.run_id)
    events = sa.Table('chat_run_events', metadata,
        sa.Column('tenant_id', sa.Uuid(), primary_key=True),
        sa.Column('run_id', sa.Uuid(), primary_key=True),
        sa.Column('sequence', sa.BigInteger(), primary_key=True),
        sa.Column('event', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','run_id'], ['agent_runs.tenant_id','agent_runs.id'], ondelete='CASCADE'),
        sa.CheckConstraint('sequence > 0', name='run_event_sequence'))
    sa.Index('ix_chat_run_events_created', events.c.created_at)
