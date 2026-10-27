"""Tenant-scoped, shared PostgreSQL model circuit state."""

import sqlalchemy as sa


def add_model_circuit(meta):
    sa.Table('model_circuit_state', meta,
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('identity_tenants.id', ondelete='RESTRICT'),
                  primary_key=True),
        sa.Column('provider', sa.Text(), primary_key=True),
        sa.Column('model', sa.Text(), primary_key=True),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('failure_count', sa.Integer(), nullable=False),
        sa.Column('open_count', sa.BigInteger(), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('open_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('probe_owner', sa.Uuid(), nullable=True),
        sa.Column('probe_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generation', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('deepseek','openai','local') AND length(model) BETWEEN 1 AND 200",
                           name='model_circuit_identity'),
        sa.CheckConstraint("state IN ('closed','open','half_open') AND failure_count BETWEEN 0 AND 3 "
                           "AND open_count >= 0 "
                           "AND generation >= 0 AND "
                           "((state = 'closed' AND open_until IS NULL AND probe_owner IS NULL AND probe_until IS NULL) "
                           "OR (state = 'open' AND open_until IS NOT NULL AND probe_owner IS NULL AND probe_until IS NULL) "
                           "OR (state = 'half_open' AND open_until IS NULL AND probe_owner IS NOT NULL AND probe_until IS NOT NULL))",
                           name='model_circuit_state_shape'))
