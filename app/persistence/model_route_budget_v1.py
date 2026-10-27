"""Immutable 0024 fallback-model token contract under the tenant's global budget."""

import sqlalchemy as sa


def add_model_route_budget(meta):
    routes = sa.Table('model_route_budget_policies', meta,
        sa.Column('route_policy_id', sa.Uuid(), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=False),
        sa.Column('publication_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('input_token_ceiling', sa.BigInteger(), nullable=False),
        sa.Column('output_token_limit', sa.BigInteger(), nullable=False),
        sa.Column('source_sha256', sa.Text(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'route_policy_id', name='uq_route_budget_tenant_id'),
        sa.UniqueConstraint('tenant_id', 'provider', 'model', 'version', name='uq_route_budget_model_version'),
        sa.UniqueConstraint('tenant_id', 'publication_id', name='uq_route_budget_publication'),
        sa.ForeignKeyConstraint(['tenant_id', 'actor_id'],
            ['identity_memberships.tenant_id', 'identity_memberships.user_id'], ondelete='RESTRICT'),
        sa.CheckConstraint("version > 0 AND provider IN ('openai','local') AND length(model) BETWEEN 1 AND 200 "
                           "AND currency IN ('CNY','USD') AND source_sha256 ~ '^[0-9a-f]{64}$'",
                           name='route_budget_identity'),
        sa.CheckConstraint('input_token_ceiling BETWEEN 1 AND 1000000000 AND '
                           'output_token_limit BETWEEN 1 AND 1000000000', name='route_budget_bounds'))
    sa.Index('ix_route_budget_tenant_model', routes.c.tenant_id, routes.c.provider,
             routes.c.model, routes.c.version)
    reservations = meta.tables['model_budget_reservations']
    reservations.append_column(sa.Column('route_policy_id', sa.Uuid(), nullable=True))
    reservations.append_constraint(sa.ForeignKeyConstraint(
        ['tenant_id', 'route_policy_id'],
        ['model_route_budget_policies.tenant_id', 'model_route_budget_policies.route_policy_id'],
        name='fk_budget_reservation_route_policy', ondelete='RESTRICT'))
