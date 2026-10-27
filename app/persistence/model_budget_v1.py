"""Immutable 0022 tenant budget policies and conservative pre-call reservations."""
import sqlalchemy as sa


def add_model_budget(meta):
    sa.Table('model_budget_policies',meta,
        sa.Column('policy_id',sa.Uuid(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),nullable=False),
        sa.Column('publication_id',sa.Uuid(),nullable=False),
        sa.Column('version',sa.BigInteger(),nullable=False),
        sa.Column('provider',sa.Text(),nullable=False),
        sa.Column('model',sa.Text(),nullable=False),
        sa.Column('currency',sa.Text(),nullable=False),
        *(sa.Column(n,sa.Numeric(30,9),nullable=False) for n in ('tenant_limit','day_limit','run_limit')),
        sa.Column('input_token_ceiling',sa.BigInteger(),nullable=False),
        sa.Column('output_token_limit',sa.BigInteger(),nullable=False),
        sa.Column('max_run_requests',sa.Integer(),nullable=False),
        sa.Column('soft_percent',sa.Integer(),nullable=False),
        sa.Column('source_sha256',sa.Text(),nullable=False),
        sa.Column('published_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','policy_id',name='uq_budget_policy_tenant_id'),
        sa.UniqueConstraint('tenant_id','version',name='uq_budget_policy_version'),
        sa.UniqueConstraint('tenant_id','publication_id',name='uq_budget_publication'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id'],['identity_memberships.tenant_id','identity_memberships.user_id'],ondelete='RESTRICT'),
        sa.CheckConstraint("version>0 AND provider IN ('deepseek','openai','local','synthetic') AND length(model) BETWEEN 1 AND 200 AND currency IN ('CNY','USD') AND source_sha256 ~ '^[0-9a-f]{64}$'",name='budget_policy_identity'),
        sa.CheckConstraint('input_token_ceiling BETWEEN 1 AND 1000000000 AND output_token_limit BETWEEN 1 AND 1000000000 AND max_run_requests BETWEEN 1 AND 10000 AND soft_percent BETWEEN 1 AND 99',name='budget_policy_bounds'),
        sa.CheckConstraint(' AND '.join(n+' BETWEEN 0 AND 1000000000000000000' for n in ('tenant_limit','day_limit','run_limit')),name='budget_policy_limits'))
    reservations=sa.Table('model_budget_reservations',meta,
        sa.Column('attempt_id',sa.Uuid(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('policy_id',sa.Uuid(),nullable=False),
        sa.Column('amount',sa.Numeric(30,9),nullable=False),
        sa.Column('currency',sa.Text(),nullable=False),
        sa.Column('output_token_limit',sa.BigInteger(),nullable=False),
        *(sa.Column(n,sa.Boolean(),nullable=False) for n in ('tenant_soft','day_soft','run_soft')),
        sa.Column('reserved_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','attempt_id'],['model_price_quotes.tenant_id','model_price_quotes.attempt_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','policy_id'],['model_budget_policies.tenant_id','model_budget_policies.policy_id'],ondelete='RESTRICT'),
        sa.CheckConstraint("amount BETWEEN 0 AND 1000000000000000000 AND currency IN ('CNY','USD') AND output_token_limit BETWEEN 1 AND 1000000000",name='budget_reservation_shape'))
    sa.Index('ix_budget_reservations_tenant',reservations.c.tenant_id)
