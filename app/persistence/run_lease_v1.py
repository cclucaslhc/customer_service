"""Immutable migration 0009: fenced live Run ownership and durable cancellation.

Existing unleased records remain untouched; this is not queue backfill or replay.
"""

import sqlalchemy as sa


def add_run_leases(metadata):
    runs = metadata.tables['agent_runs']
    runs.append_column(sa.Column('lease_owner', sa.Uuid(), nullable=True))
    runs.append_column(sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True))
    runs.append_column(sa.Column('lease_fence', sa.BigInteger(), nullable=False, server_default='0'))
    runs.append_column(sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True))
    runs.append_constraint(sa.CheckConstraint(
        '(lease_owner IS NULL AND lease_until IS NULL AND lease_fence = 0) OR '
        '(lease_owner IS NOT NULL AND lease_until IS NOT NULL AND lease_fence > 0)',
        name='run_lease_tuple'))
    sa.Index('uq_agent_runs_live_leased_session', runs.c.session_id, unique=True,
             postgresql_where=sa.text("status = 'running' AND lease_owner IS NOT NULL"))
    sa.Index('ix_agent_runs_lease_expiry', runs.c.lease_until,
             postgresql_where=sa.text("status = 'running' AND lease_owner IS NOT NULL"))
