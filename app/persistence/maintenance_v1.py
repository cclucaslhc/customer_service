"""Immutable 0017 maintenance gate and statement-write fencing."""
import sqlalchemy as sa

WRITE_GATE_KEY = 1129533506
PROTECTED_TABLES = tuple('agent_run_tools agent_runs app_settings audit_logs chat_admission_policy chat_run_events chat_run_queue chat_run_snapshots chat_sessions contacts documents identity_events identity_login_buckets identity_login_slots identity_memberships identity_sessions identity_tenants identity_users knowledge_assets knowledge_chunks knowledge_job_items knowledge_job_queue knowledge_jobs knowledge_settings messages migration_batches prompt_versions provider_configs session_notes storage_configs'.split())


def add_maintenance(meta):
    sa.Table('maintenance_state',meta,
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('blocked',sa.Boolean(),nullable=False),
        sa.Column('owner',sa.Uuid(),nullable=True),
        sa.Column('fence',sa.BigInteger(),nullable=False),
        sa.Column('lease_until',sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint('id = 1 AND fence > 0',name='maintenance_singleton_fence'),
        sa.CheckConstraint('(blocked AND owner IS NOT NULL AND lease_until IS NOT NULL) OR (NOT blocked AND owner IS NULL AND lease_until IS NULL)',name='maintenance_owner'))


WRITE_FUNCTION_SQL = f"""
CREATE FUNCTION public.cs_maintenance_write_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE AS $$
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'Maintenance fencing requires read committed writes' USING ERRCODE = '55000';
  END IF;
  PERFORM pg_advisory_xact_lock_shared({WRITE_GATE_KEY});
  IF EXISTS (SELECT 1 FROM public.maintenance_state WHERE id=1 AND blocked) THEN
    RAISE EXCEPTION 'Database maintenance write hold' USING ERRCODE = '55000';
  END IF;
  RETURN NULL;
END $$
"""
