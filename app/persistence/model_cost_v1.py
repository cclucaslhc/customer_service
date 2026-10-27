"""Immutable 0021 prices, pre-call quotes, and append-only cost statements."""
import sqlalchemy as sa

RATES = ('input_rate','output_rate','cache_read_rate','cache_write_rate')


def add_model_cost(meta):
    prices = sa.Table('model_price_versions',meta,
        sa.Column('price_id',sa.Uuid(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('publication_id',sa.Uuid(),nullable=False),
        sa.Column('actor_id',sa.Uuid(),nullable=False),
        sa.Column('provider',sa.Text(),nullable=False),
        sa.Column('model',sa.Text(),nullable=False),
        sa.Column('version',sa.BigInteger(),nullable=False),
        sa.Column('currency',sa.Text(),nullable=False),
        *(sa.Column(name,sa.Numeric(16,9),nullable=False) for name in RATES),
        sa.Column('source_sha256',sa.Text(),nullable=False),
        sa.Column('published_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','price_id',name='uq_model_price_tenant_id'),
        sa.UniqueConstraint('tenant_id','publication_id',name='uq_model_price_publication'),
        sa.UniqueConstraint('tenant_id','provider','model','version',name='uq_model_price_version'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id'],['identity_memberships.tenant_id','identity_memberships.user_id'],ondelete='RESTRICT'),
        sa.CheckConstraint("provider IN ('deepseek','openai','local','synthetic','unknown') AND length(model) BETWEEN 1 AND 200 AND version > 0 AND currency IN ('CNY','USD') AND source_sha256 ~ '^[0-9a-f]{64}$'",name='model_price_identity'),
        sa.CheckConstraint(' AND '.join(name+' BETWEEN 0 AND 1000000' for name in RATES),name='model_price_rates'))
    attempts = meta.tables['model_usage_attempts']
    attempts.append_constraint(sa.UniqueConstraint('tenant_id','attempt_id',name='uq_model_usage_tenant_attempt'))
    sa.Table('model_price_quotes',meta,
        sa.Column('attempt_id',sa.Uuid(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('price_id',sa.Uuid(),nullable=True),
        sa.Column('quoted_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('tenant_id','attempt_id',name='uq_model_quote_tenant_attempt'),
        sa.ForeignKeyConstraint(['tenant_id','attempt_id'],['model_usage_attempts.tenant_id','model_usage_attempts.attempt_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','price_id'],['model_price_versions.tenant_id','model_price_versions.price_id'],ondelete='RESTRICT'))
    costs = sa.Table('model_cost_assessments',meta,
        sa.Column('assessment_id',sa.Uuid(),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),nullable=False),
        sa.Column('attempt_id',sa.Uuid(),nullable=False),
        sa.Column('revision',sa.BigInteger(),nullable=False),
        sa.Column('kind',sa.Text(),nullable=False),
        sa.Column('status',sa.Text(),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('amount',sa.Numeric(30,9),nullable=True),
        sa.Column('currency',sa.Text(),nullable=True),
        sa.Column('receipt_sha256',sa.Text(),nullable=True),
        sa.Column('request_id',sa.Uuid(),nullable=True),
        sa.Column('actor_id',sa.Uuid(),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','attempt_id'],['model_usage_attempts.tenant_id','model_usage_attempts.attempt_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id'],['identity_memberships.tenant_id','identity_memberships.user_id'],ondelete='RESTRICT'),
        sa.UniqueConstraint('tenant_id','attempt_id','revision',name='uq_model_cost_revision'),
        sa.UniqueConstraint('tenant_id','request_id',name='uq_model_cost_request'),
        sa.UniqueConstraint('tenant_id','receipt_sha256',name='uq_model_cost_receipt'),
        sa.CheckConstraint("(kind='estimate' AND revision=0 AND actor_id IS NULL AND receipt_sha256 IS NULL AND request_id IS NULL AND ((status='estimated' AND reason='sdk_observed' AND amount IS NOT NULL AND amount >= 0 AND currency IS NOT NULL AND currency IN ('CNY','USD')) OR (status='unknown' AND reason IN ('no_quote','no_price','unknown_usage','partial_usage','invalid_cache') AND amount IS NULL AND (currency IS NULL OR currency IN ('CNY','USD'))))) OR (kind='reconcile' AND revision>0 AND status='operator_reported' AND reason='operator_statement' AND amount IS NOT NULL AND amount BETWEEN 0 AND 1000000000000000000 AND currency IS NOT NULL AND currency IN ('CNY','USD') AND actor_id IS NOT NULL AND request_id IS NOT NULL AND receipt_sha256 IS NOT NULL AND receipt_sha256 ~ '^[0-9a-f]{64}$')",name='model_cost_shape'))
    sa.Index('ix_model_cost_tenant_attempt_revision',costs.c.tenant_id,costs.c.attempt_id,costs.c.revision)


NO_CHANGE_SQL = """
CREATE FUNCTION public.cs_cost_evidence_no_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Cost evidence is append-only' USING ERRCODE='55000';
END $$
"""
