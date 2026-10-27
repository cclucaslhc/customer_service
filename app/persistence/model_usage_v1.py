"""Immutable 0020 SDK-observed usage attempts; not prices or provider invoices."""
import sqlalchemy as sa


def add_model_usage(meta):
    sa.Table('model_usage_attempts', meta,
        sa.Column('attempt_id', sa.Uuid(), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('lease_owner', sa.Uuid(), nullable=False),
        sa.Column('lease_fence', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('usage_status', sa.Text(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=True) for name in
          ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id','run_id'],['agent_runs.tenant_id','agent_runs.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','actor_id'],['identity_memberships.tenant_id','identity_memberships.user_id'], ondelete='RESTRICT'),
        sa.CheckConstraint("provider IN ('deepseek','openai','local','synthetic','unknown') AND purpose='chat' AND length(model) BETWEEN 1 AND 200 AND lease_fence > 0",name='model_usage_identity'),
        sa.CheckConstraint("state IN ('started','completed','error','cancelled','incomplete') AND usage_status IN ('unknown','observed','partial')",name='model_usage_states'),
        sa.CheckConstraint("(state='started' AND finished_at IS NULL AND usage_status='unknown') OR (state<>'started' AND finished_at IS NOT NULL AND finished_at >= started_at)",name='model_usage_time'),
        sa.CheckConstraint("(usage_status='unknown' AND input_tokens IS NULL AND output_tokens IS NULL AND cache_read_tokens IS NULL AND cache_write_tokens IS NULL) OR (usage_status<>'unknown' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cache_read_tokens IS NOT NULL AND cache_write_tokens IS NOT NULL AND input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 AND cache_write_tokens >= 0 AND cache_read_tokens <= input_tokens AND cache_write_tokens <= input_tokens AND (input_tokens > 0 OR output_tokens > 0))",name='model_usage_tokens'),
        sa.CheckConstraint("usage_status <> 'observed' OR state='completed'",name='model_usage_observed'),
        sa.Index('ix_model_usage_tenant_run_started','tenant_id','run_id','started_at'))


IMMUTABLE_SQL = """
CREATE FUNCTION public.cs_model_usage_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
   RAISE EXCEPTION 'Model usage evidence cannot be deleted' USING ERRCODE='55000';
 END IF;
 IF OLD.state <> 'started' OR NEW.state = 'started' OR
    (to_jsonb(NEW) - ARRAY['state','usage_status','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','finished_at'])
      IS DISTINCT FROM
    (to_jsonb(OLD) - ARRAY['state','usage_status','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','finished_at']) THEN
   RAISE EXCEPTION 'Model usage evidence cannot be rewritten' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$
"""
