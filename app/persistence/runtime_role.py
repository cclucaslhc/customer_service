"""Fail-closed startup check for the PostgreSQL API/Worker identity."""

import sqlalchemy as sa


def require_application_role(db, kind='api'):
    """Check effective privileges, not a caller-supplied role name.

    This is a startup gate, not RLS or protection against a database owner
    changing grants after startup. The approved grant script is the source of
    the required privileges.
    """
    if kind not in {'api','worker'}:
        raise ValueError('Invalid PostgreSQL runtime role kind')
    db.execute(sa.text("SET LOCAL statement_timeout='5s'"))
    row = db.execute(sa.text("""
        SELECT NOT (
            r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
            OR r.rolbypassrls OR r.rolinherit
            OR d.datdba = r.oid
            OR pg_has_role(r.oid, d.datdba, 'MEMBER')
            OR has_database_privilege(current_user, current_database(), 'CREATE')
            OR has_schema_privilege(current_user, 'public', 'CREATE')
            OR EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
                  AND (c.relowner=r.oid OR pg_has_role(r.oid,c.relowner,'MEMBER'))
            )
            OR EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')
                  AND (has_table_privilege(current_user,c.oid,'TRUNCATE')
                       OR has_table_privilege(current_user,c.oid,'TRIGGER'))
            )
        )
        AND has_database_privilege(current_user,current_database(),'CONNECT')
        AND has_schema_privilege(current_user,'public','USAGE')
        AND has_table_privilege(current_user,'public.agent_runs','SELECT')
        AND has_table_privilege(current_user,'public.agent_runs','INSERT')
        AND has_table_privilege(current_user,'public.agent_runs','UPDATE')
        AND has_table_privilege(current_user,'public.agent_runs','DELETE')
        AND has_table_privilege(current_user,'public.audit_logs','SELECT')
        AND has_table_privilege(current_user,'public.audit_logs','INSERT')
        AND NOT has_table_privilege(current_user,'public.audit_logs','UPDATE')
        AND NOT has_table_privilege(current_user,'public.audit_logs','DELETE')
        AND has_table_privilege(current_user,'public.model_usage_attempts','INSERT')
        AND has_table_privilege(current_user,'public.model_usage_attempts','UPDATE')
        AND NOT has_table_privilege(current_user,'public.model_usage_attempts','DELETE')
        AND has_table_privilege(current_user,'public.model_circuit_state','SELECT')
        AND has_table_privilege(current_user,'public.model_circuit_state','INSERT')
        AND has_table_privilege(current_user,'public.model_circuit_state','UPDATE')
        AND NOT has_table_privilege(current_user,'public.model_circuit_state','DELETE')
        AND has_table_privilege(current_user,'public.knowledge_admission_policy','SELECT')
        AND NOT has_table_privilege(current_user,'public.knowledge_admission_policy','INSERT')
        AND NOT has_table_privilege(current_user,'public.knowledge_admission_policy','UPDATE')
        AND NOT has_table_privilege(current_user,'public.knowledge_admission_policy','DELETE')
        AND NOT has_table_privilege(current_user,'public.embedding_profiles','INSERT')
        AND NOT has_table_privilege(current_user,'public.embedding_profiles','UPDATE')
        AND NOT has_table_privilege(current_user,'public.embedding_profiles','DELETE')
        AND NOT has_table_privilege(current_user,'public.knowledge_index_versions','INSERT')
        AND NOT has_table_privilege(current_user,'public.knowledge_index_versions','UPDATE')
        AND NOT has_table_privilege(current_user,'public.knowledge_index_versions','DELETE')
        AND NOT has_table_privilege(current_user,'public.rag_release_intents','SELECT')
        AND NOT has_table_privilege(current_user,'public.rag_release_intents','INSERT')
        AND NOT has_table_privilege(current_user,'public.rag_release_intents','UPDATE')
        AND NOT has_table_privilege(current_user,'public.rag_release_intents','DELETE')
        AND NOT has_table_privilege(current_user,'public.rag_rollouts','SELECT')
        AND NOT has_table_privilege(current_user,'public.rag_rollouts','INSERT')
        AND NOT has_table_privilege(current_user,'public.rag_rollouts','UPDATE')
        AND NOT has_table_privilege(current_user,'public.rag_rollouts','DELETE')
        AND has_function_privilege(current_user,'public.cs_rag_route(uuid,text)','EXECUTE')
        AND has_function_privilege(current_user,'public.cs_rag_route_lock(uuid,text)','EXECUTE')
        AND has_function_privilege(current_user,'public.cs_rag_route_context(uuid,text,text,text)','EXECUTE')
        AND NOT has_table_privilege(current_user,'public.knowledge_embeddings_1024','UPDATE')
        AND NOT has_table_privilege(current_user,'public.knowledge_embeddings_1024','DELETE')
        AND (
            (:kind='api' AND has_table_privilege(current_user,'public.provider_configs','UPDATE')
                AND NOT has_table_privilege(current_user,'public.knowledge_embeddings_1024','INSERT'))
            OR (:kind='worker'
                AND has_table_privilege(current_user,'public.knowledge_embeddings_1024','INSERT')
                AND NOT has_table_privilege(current_user,'public.provider_configs','INSERT')
                AND NOT has_table_privilege(current_user,'public.provider_configs','UPDATE')
                AND NOT has_table_privilege(current_user,'public.provider_configs','DELETE')
                AND NOT has_table_privilege(current_user,'public.identity_sessions','INSERT')
                AND NOT has_table_privilege(current_user,'public.identity_sessions','UPDATE')
                AND NOT has_table_privilege(current_user,'public.identity_sessions','DELETE')
                AND NOT has_column_privilege(current_user,'public.identity_users','password_hash','SELECT')
                AND has_column_privilege(current_user,'public.identity_users','auth_version','SELECT')
                AND NOT has_table_privilege(current_user,'public.model_price_versions','INSERT')
                AND NOT has_table_privilege(current_user,'public.model_budget_policies','INSERT')
                AND NOT has_table_privilege(current_user,'public.model_route_budget_policies','INSERT'))
        )
        AS acceptable
        FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
        WHERE r.rolname=current_user
    """),{'kind':kind}).scalar_one_or_none()
    if row is not True:
        raise RuntimeError('Unsafe PostgreSQL application role')
