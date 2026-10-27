-- Run with psql -X -v ON_ERROR_STOP=1 as the non-superuser migration owner,
-- after upgrading this database to the current Alembic head. Roles must
-- already exist and must not inherit one another. No passwords are handled.
-- Required -v variables: migration_role, app_role, worker_role, backup_role.
-- Review database-wide PUBLIC revocation before using outside isolation.
SELECT (
    current_user = :'migration_role'
    AND (SELECT pg_get_userbyid(datdba) FROM pg_database
         WHERE datname = current_database()) = :'migration_role'
    AND (SELECT count(*) FROM pg_roles WHERE rolname IN
         (:'migration_role', :'app_role', :'worker_role', :'backup_role')
         AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
         AND NOT rolinherit) = 4
    AND NOT pg_has_role(:'app_role', :'migration_role', 'MEMBER')
    AND NOT pg_has_role(:'worker_role', :'migration_role', 'MEMBER')
    AND NOT pg_has_role(:'backup_role', :'migration_role', 'MEMBER')
    AND NOT pg_has_role(:'app_role', :'backup_role', 'MEMBER')
    AND NOT pg_has_role(:'worker_role', :'backup_role', 'MEMBER')
    AND NOT pg_has_role(:'app_role', :'worker_role', 'MEMBER')
    AND NOT pg_has_role(:'worker_role', :'app_role', 'MEMBER')
) AS cs_roles_safe \gset
\if :cs_roles_safe
\else
    -- ON_ERROR_STOP makes unsafe role ownership/membership fatal.
    SELECT 1 / 0;
\endif
BEGIN;
SELECT current_database() AS cs_database \gset

REVOKE ALL ON DATABASE :"cs_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"cs_database" TO :"app_role", :"worker_role", :"backup_role";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_role", :"worker_role", :"backup_role";

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"worker_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"worker_role";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"backup_role";
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO :"backup_role";

-- Runtime accounting and audit evidence must not be rewritten by the app
-- role even if a trigger is inadvertently disabled by a future migration.
REVOKE UPDATE, DELETE ON TABLE public.audit_logs, public.identity_events,
    public.model_price_versions, public.model_price_quotes,
    public.model_cost_assessments, public.model_budget_policies,
    public.model_budget_reservations, public.model_route_budget_policies FROM :"app_role";
REVOKE DELETE ON TABLE public.model_usage_attempts FROM :"app_role";
REVOKE DELETE ON TABLE public.model_circuit_state FROM :"app_role";
REVOKE INSERT, UPDATE, DELETE ON TABLE public.knowledge_admission_policy FROM :"app_role";
REVOKE INSERT, UPDATE, DELETE ON TABLE public.embedding_profiles, public.knowledge_index_versions FROM :"app_role";
REVOKE ALL ON TABLE public.rag_release_intents FROM :"app_role";
REVOKE ALL ON TABLE public.rag_rollouts FROM :"app_role";
REVOKE UPDATE, DELETE ON TABLE public.knowledge_chunk_versions FROM :"app_role";
REVOKE INSERT, UPDATE, DELETE ON TABLE public.knowledge_embeddings_1024 FROM :"app_role";
REVOKE UPDATE, DELETE ON TABLE public.audit_logs, public.identity_events,
    public.model_price_versions, public.model_price_quotes,
    public.model_cost_assessments, public.model_budget_policies,
    public.model_budget_reservations, public.model_route_budget_policies FROM :"worker_role";
REVOKE DELETE ON TABLE public.model_usage_attempts FROM :"worker_role";
REVOKE DELETE ON TABLE public.model_circuit_state FROM :"worker_role";
REVOKE INSERT, UPDATE, DELETE ON TABLE public.knowledge_admission_policy FROM :"worker_role";
REVOKE INSERT, UPDATE, DELETE ON TABLE public.embedding_profiles, public.knowledge_index_versions FROM :"worker_role";
REVOKE ALL ON TABLE public.rag_release_intents FROM :"worker_role";
REVOKE ALL ON TABLE public.rag_rollouts FROM :"worker_role";
GRANT EXECUTE ON FUNCTION public.cs_rag_route(uuid,text) TO :"app_role", :"worker_role";
GRANT EXECUTE ON FUNCTION public.cs_rag_route_lock(uuid,text) TO :"app_role", :"worker_role";
GRANT EXECUTE ON FUNCTION public.cs_rag_route_context(uuid,text,text,text) TO :"app_role", :"worker_role";
REVOKE UPDATE, DELETE ON TABLE public.knowledge_chunk_versions FROM :"worker_role";
REVOKE UPDATE, DELETE ON TABLE public.knowledge_embeddings_1024 FROM :"worker_role";
REVOKE INSERT ON TABLE public.model_price_versions, public.model_budget_policies,
    public.model_route_budget_policies FROM :"worker_role";

-- Workers consume already-authorized configuration and delegated sessions;
-- they never publish tenant settings or provision identities.
REVOKE INSERT, UPDATE, DELETE ON TABLE public.provider_configs,
    public.app_settings, public.prompt_versions, public.storage_configs,
    public.knowledge_settings, public.identity_tenants, public.identity_users,
    public.identity_memberships, public.identity_sessions, public.identity_events
    FROM :"worker_role";

-- Session validation only needs these user fields. Never expose password
-- verifiers to the cross-tenant queue worker through table-wide SELECT.
REVOKE SELECT ON TABLE public.identity_users FROM :"worker_role";
GRANT SELECT (id, active, auth_version) ON TABLE public.identity_users TO :"worker_role";

-- Future migration-owned objects receive the same baseline. Any new
-- append-only table must be explicitly reviewed and restricted after upgrade.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"worker_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"backup_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"worker_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role" IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO :"backup_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_role"
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Fail before COMMIT if inherited/previous privileges still make either
-- runtime identity an owner, schema creator, TRUNCATE-capable writer or a
-- backup identity with DML. A GRANT script cannot repair arbitrary role
-- membership or ownership; the operator must fix those separately.
SELECT (
    NOT has_database_privilege(:'app_role', current_database(), 'CREATE')
    AND NOT has_schema_privilege(:'app_role', 'public', 'CREATE')
    AND NOT has_database_privilege(:'worker_role', current_database(), 'CREATE')
    AND NOT has_schema_privilege(:'worker_role', 'public', 'CREATE')
    AND NOT has_database_privilege(:'backup_role', current_database(), 'CREATE')
    AND NOT has_schema_privilege(:'backup_role', 'public', 'CREATE')
    AND NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S')
          AND pg_get_userbyid(c.relowner) IN (:'app_role', :'worker_role', :'backup_role')
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
          AND (has_table_privilege(:'app_role', c.oid, 'TRUNCATE')
               OR has_table_privilege(:'worker_role', c.oid, 'TRUNCATE')
               OR has_table_privilege(:'backup_role', c.oid, 'INSERT')
               OR has_table_privilege(:'backup_role', c.oid, 'UPDATE')
               OR has_table_privilege(:'backup_role', c.oid, 'DELETE'))
    )
    AND NOT has_table_privilege(:'app_role', 'public.audit_logs', 'UPDATE')
    AND NOT has_table_privilege(:'app_role', 'public.audit_logs', 'DELETE')
    AND NOT has_table_privilege(:'app_role', 'public.model_usage_attempts', 'DELETE')
    AND NOT has_table_privilege(:'worker_role', 'public.model_usage_attempts', 'DELETE')
    AND NOT has_table_privilege(:'worker_role', 'public.provider_configs', 'UPDATE')
    AND has_table_privilege(:'app_role', 'public.model_circuit_state', 'UPDATE')
    AND has_table_privilege(:'worker_role', 'public.model_circuit_state', 'UPDATE')
    AND NOT has_table_privilege(:'app_role', 'public.model_circuit_state', 'DELETE')
    AND NOT has_table_privilege(:'worker_role', 'public.model_circuit_state', 'DELETE')
    AND NOT has_table_privilege(:'worker_role', 'public.identity_sessions', 'INSERT')
    AND NOT has_column_privilege(:'worker_role', 'public.identity_users', 'password_hash', 'SELECT')
    AND has_column_privilege(:'worker_role', 'public.identity_users', 'auth_version', 'SELECT')
    AND has_table_privilege(:'app_role', 'public.provider_configs', 'UPDATE')
) AS cs_grants_safe \gset
\if :cs_grants_safe
\else
    SELECT 1 / 0;
\endif
COMMIT;
