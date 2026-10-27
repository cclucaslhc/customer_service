"""Typed chat queue and ordered private event ledger; no automatic model replay."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.identity_schema_v1 import metadata as identity
from app.persistence.durable_chat_v1 import add_durable_chat

revision = '0010_durable_chat'
down_revision = '0009_run_leases'
branch_labels = None
depends_on = None


def upgrade():
    # Minimal immutable references for DDL, independent of future runtime schema.
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    for table in baseline.tables.values():
        table.to_metadata(meta)
    for table in identity.tables.values():
        table.to_metadata(meta)
    for name in ('messages','agent_runs'):
        meta.tables[name].append_column(sa.Column('tenant_id', sa.Uuid()))
        if name == 'agent_runs':
            meta.tables[name].append_constraint(sa.UniqueConstraint('tenant_id','id'))
    add_durable_chat(meta)
    op.add_column('agent_runs', sa.Column('execution_mode', sa.Text(), nullable=False, server_default='direct'))
    op.add_column('agent_runs', sa.Column('event_sequence', sa.BigInteger(), nullable=False, server_default='0'))
    op.create_check_constraint('run_execution_mode','agent_runs',"execution_mode IN ('direct','queued') AND event_sequence >= 0")
    op.create_index('uq_agent_runs_queued_active_session','agent_runs',['session_id'],unique=True,
        postgresql_where=sa.text("execution_mode = 'queued' AND status IN ('queued','running')"))
    op.create_unique_constraint('uq_messages_tenant_id','messages',['tenant_id','id'])
    op.create_unique_constraint('uq_identity_sessions_scope_hash','identity_sessions',['tenant_id','user_id','token_hash'])
    meta.tables['chat_run_queue'].create(op.get_bind(), checkfirst=False)
    meta.tables['chat_run_events'].create(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError('Durable chat downgrade is disabled: queued commands and private events must be preserved')
