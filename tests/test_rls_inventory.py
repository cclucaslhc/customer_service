"""Keep every PostgreSQL table classified before tenant RLS is rolled out.

This is an inventory gate, not evidence that RLS policies are installed.
"""

from app.persistence.schema import metadata


TENANT_TABLES = {
    'agent_run_tools', 'agent_runs', 'app_settings', 'audit_logs',
    'chat_run_events', 'chat_run_queue', 'chat_run_snapshots',
    'chat_sessions', 'contacts', 'documents', 'identity_events',
    'identity_memberships', 'identity_sessions', 'knowledge_assets',
    'knowledge_chunks', 'knowledge_job_items', 'knowledge_job_queue',
    'knowledge_jobs', 'knowledge_settings', 'messages',
    'model_budget_policies', 'model_budget_reservations',
    'model_cost_assessments', 'model_route_budget_policies', 'model_circuit_state', 'model_price_quotes',
    'model_price_versions', 'model_usage_attempts', 'prompt_versions',
    'provider_configs', 'session_notes', 'storage_configs',
    'embedding_profiles', 'knowledge_index_versions',
    'knowledge_chunk_versions',
    'knowledge_embeddings_1024',
    'rag_release_intents',
    'rag_rollouts',
}

GLOBAL_TABLES = {
    'backup_jobs', 'backup_schedules', 'chat_admission_policy',
    'identity_login_buckets', 'identity_login_slots', 'identity_tenants',
    'identity_users', 'maintenance_state', 'migration_batches',
    'recovery_state', 'knowledge_admission_policy',
}


def test_every_table_has_explicit_rls_classification():
    assert TENANT_TABLES.isdisjoint(GLOBAL_TABLES)
    assert set(metadata.tables) == TENANT_TABLES | GLOBAL_TABLES
    for name in TENANT_TABLES:
        assert 'tenant_id' in metadata.tables[name].c, name
    for name in GLOBAL_TABLES:
        assert 'tenant_id' not in metadata.tables[name].c, name


def test_nullable_tenant_ids_are_an_explicit_exception():
    nullable = {
        name for name in TENANT_TABLES
        if metadata.tables[name].c.tenant_id.nullable
    }
    assert nullable == {'identity_events'}
