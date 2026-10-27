"""Fenced PostgreSQL Run ownership; never replay existing model/tool calls."""

import sqlalchemy as sa
from alembic import op

revision = '0009_run_leases'
down_revision = '0008_login_admission'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_runs', sa.Column('lease_owner', sa.Uuid(), nullable=True))
    op.add_column('agent_runs', sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('agent_runs', sa.Column('lease_fence', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('agent_runs', sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint('run_lease_tuple', 'agent_runs',
        '(lease_owner IS NULL AND lease_until IS NULL AND lease_fence = 0) OR '
        '(lease_owner IS NOT NULL AND lease_until IS NOT NULL AND lease_fence > 0)')
    op.create_index('uq_agent_runs_live_leased_session', 'agent_runs', ['session_id'], unique=True,
                    postgresql_where=sa.text("status = 'running' AND lease_owner IS NOT NULL"))
    op.create_index('ix_agent_runs_lease_expiry', 'agent_runs', ['lease_until'],
                    postgresql_where=sa.text("status = 'running' AND lease_owner IS NOT NULL"))


def downgrade():
    raise RuntimeError('Run lease downgrade is disabled; it would remove fencing and cancellation')
