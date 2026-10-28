"""Isolated real-PostgreSQL proof of migration, runtime and backup boundaries."""

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.auth_http import AuthPolicy
from app.main import create_app
from app.persistence.engine import build_engine
from app.persistence.bundle import PostgreSQLRepositories
from app.security import SecretStore


ROOT = Path(__file__).resolve().parents[1]
GRANTS = ROOT / 'ops' / 'postgresql' / 'grant-runtime.sql'
SYNTHETIC_PASSWORD = 'local-role-test-only'


def _denied(engine, sql):
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as db:
            db.exec_driver_sql(sql)


def test_isolated_migration_runtime_backup_roles_and_revocation(monkeypatch, tmp_path):
    source = os.environ.get('TEST_POSTGRES_URL')
    psql = os.environ.get('TEST_PSQL_BIN')
    pg_dump = os.environ.get('TEST_PG_DUMP_BIN')
    if not source or not psql or not pg_dump:
        pytest.skip('Dedicated local PostgreSQL and explicit binary paths required')
    parsed = make_url(source)
    if (parsed.host != '::1' or parsed.port != 55433 or parsed.database != 'postgres'
        or parsed.username != 'integration' or parsed.password != 'local-integration-only'):
        pytest.skip('Role test is restricted to the named disposable integration cluster')
    for binary in (psql, pg_dump):
        if not Path(binary).is_file():
            pytest.fail('Explicit PostgreSQL binary is missing')

    suffix = uuid.uuid4().hex
    database = 'cs_roles_' + suffix
    migrator, app, worker, backup = ('cs_migrator_' + suffix, 'cs_app_' + suffix,
        'cs_worker_' + suffix, 'cs_backup_' + suffix)
    admin = build_engine(source, pool_size=1)
    migrator_engine = app_engine = worker_engine = backup_engine = None
    created_roles = []
    created_database = False
    try:
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            for name in (migrator, app, worker, backup):
                db.exec_driver_sql(f'CREATE ROLE "{name}" LOGIN PASSWORD \'{SYNTHETIC_PASSWORD}\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION')
                created_roles.append(name)
            db.exec_driver_sql(f'CREATE DATABASE "{database}" OWNER "{migrator}"')
            created_database = True

        def role_url(name):
            return parsed.set(username=name, password=SYNTHETIC_PASSWORD,
                database=database).render_as_string(hide_password=False)

        # Extension installation is a DBA prerequisite, not a privilege granted
        # to the migration/runtime roles. Match the normal integration fixture.
        extension_admin = build_engine(parsed.set(database=database).render_as_string(hide_password=False),pool_size=1)
        try:
            with extension_admin.begin() as db:
                db.exec_driver_sql('CREATE EXTENSION vector')
        finally:
            extension_admin.dispose()
        migrator_engine = build_engine(role_url(migrator), pool_size=1)
        monkeypatch.setenv('MIGRATION_DATABASE_URL', role_url(migrator))
        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
        bad_operator = subprocess.run([psql, '-X', '-q', '-v', 'ON_ERROR_STOP=1',
            '-v', f'migration_role={migrator}', '-v', f'app_role={app}', '-v', f'worker_role={worker}',
            '-v', f'backup_role={backup}', '-h', '::1', '-p', '55433',
            '-U', 'integration', '-d', database, '-f', str(GRANTS)],
            env={**os.environ, 'PGPASSWORD': 'local-integration-only'},
            capture_output=True, text=True, timeout=30)
        assert bad_operator.returncode != 0
        with migrator_engine.begin() as db:
            assert not db.exec_driver_sql('SELECT has_table_privilege(%s, %s, %s)',
                (app, 'public.agent_runs', 'SELECT')).scalar_one()
        test_env = {**os.environ, 'PGPASSWORD': SYNTHETIC_PASSWORD}
        grant_args = [psql, '-X', '-q', '-v', 'ON_ERROR_STOP=1',
            '-v', f'migration_role={migrator}', '-v', f'app_role={app}', '-v', f'worker_role={worker}',
            '-v', f'backup_role={backup}', '-h', '::1', '-p', '55433',
            '-U', migrator, '-d', database, '-f', str(GRANTS)]
        with migrator_engine.begin() as db:
            db.exec_driver_sql(f'GRANT INSERT ON public.agent_runs TO "{backup}"')
        bad_acl = subprocess.run(grant_args, env=test_env, capture_output=True, text=True, timeout=30)
        assert bad_acl.returncode != 0
        with migrator_engine.begin() as db:
            assert not db.exec_driver_sql('SELECT has_table_privilege(%s, %s, %s)',
                (app, 'public.agent_runs', 'SELECT')).scalar_one()
            db.exec_driver_sql(f'REVOKE INSERT ON public.agent_runs FROM "{backup}"')
        result = subprocess.run(grant_args,
            env=test_env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        again = subprocess.run(result.args, env=test_env, capture_output=True, text=True, timeout=30)
        assert again.returncode == 0, again.stderr

        app_engine = build_engine(role_url(app), pool_size=1)
        worker_engine = build_engine(role_url(worker), pool_size=1)
        backup_engine = build_engine(role_url(backup), pool_size=1)
        with migrator_engine.begin() as db:
            db.exec_driver_sql('CREATE TABLE public.cs_runtime_privilege_probe '
                '(id integer PRIMARY KEY, value text NOT NULL)')
            roles = db.exec_driver_sql('SELECT rolname, rolsuper, rolcreatedb, rolcreaterole '
                'FROM pg_roles WHERE rolname IN (%s,%s,%s,%s)', (migrator, app, worker, backup)).all()
            assert len(roles) == 4
            assert all(not row[1] and not row[2] and not row[3] for row in roles)

        with app_engine.begin() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM public.agent_runs').scalar_one() == 0
            db.exec_driver_sql("INSERT INTO public.cs_runtime_privilege_probe VALUES (1,'new')")
            db.exec_driver_sql("UPDATE public.cs_runtime_privilege_probe SET value='updated' WHERE id=1")
            assert db.exec_driver_sql('SELECT value FROM public.cs_runtime_privilege_probe WHERE id=1').scalar_one() == 'updated'
            db.exec_driver_sql('DELETE FROM public.cs_runtime_privilege_probe WHERE id=1')
            assert db.exec_driver_sql('SELECT count(*) FROM public.cs_runtime_privilege_probe').scalar_one() == 0
            db.exec_driver_sql("INSERT INTO public.cs_runtime_privilege_probe VALUES (3,'backup-evidence')")
            assert not db.exec_driver_sql("SELECT has_schema_privilege(current_user,'public','CREATE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_database_privilege(current_user,current_database(),'CREATE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.audit_logs','UPDATE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_usage_attempts','DELETE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_circuit_state','DELETE')").scalar_one()
        _denied(app_engine, 'CREATE TABLE public.cs_runtime_forbidden (id integer)')
        _denied(app_engine, 'ALTER TABLE public.cs_runtime_privilege_probe ADD COLUMN forbidden integer')
        _denied(app_engine, 'TRUNCATE public.cs_runtime_privilege_probe')
        _denied(app_engine, 'UPDATE public.audit_logs SET summary=summary')
        with worker_engine.begin() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM public.cs_runtime_privilege_probe').scalar_one() == 1
            db.exec_driver_sql("INSERT INTO public.cs_runtime_privilege_probe VALUES (4,'worker')")
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.provider_configs','UPDATE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_budget_policies','INSERT')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_route_budget_policies','INSERT')").scalar_one()
            assert db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_circuit_state','UPDATE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.model_circuit_state','DELETE')").scalar_one()
            assert not db.exec_driver_sql("SELECT has_column_privilege(current_user,'public.identity_users','password_hash','SELECT')").scalar_one()
            assert db.exec_driver_sql("SELECT has_column_privilege(current_user,'public.identity_users','auth_version','SELECT')").scalar_one()
        _denied(worker_engine, "UPDATE public.provider_configs SET model='forbidden'")
        _denied(worker_engine, "INSERT INTO public.model_budget_policies (policy_id) VALUES ('00000000-0000-0000-0000-000000000001')")
        _denied(worker_engine, "INSERT INTO public.model_route_budget_policies (route_policy_id) VALUES ('00000000-0000-0000-0000-000000000001')")
        _denied(worker_engine, 'SELECT password_hash FROM public.identity_users')

        runtime = PostgreSQLRepositories.from_url(role_url(app), pool_size=1, strict_role=True)
        try:
            runtime.check_schema()
            with migrator_engine.begin() as db:
                db.exec_driver_sql(f'GRANT CREATE ON SCHEMA public TO "{app}"')
            with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                runtime.check_schema()
            with migrator_engine.begin() as db:
                db.exec_driver_sql(f'REVOKE CREATE ON SCHEMA public FROM "{app}"')
            runtime.check_schema()
            tenant = runtime.identity.create_tenant('Synthetic role acceptance')
            runtime.identity.provision_user('synthetic.role.admin', SYNTHETIC_PASSWORD, tenant, role='admin')
            token = runtime.identity.login('synthetic.role.admin', SYNTHETIC_PASSWORD, tenant).token
            runtime.config.authenticated(token).initialize_tenant('2026-10-10T00:00:00+00:00')
            assert runtime.config.authenticated(token).active_provider()['provider'] == 'deepseek'
            from app.local_inference_usage import AuditInferenceMeter
            from app.rag_model_manifest import EMBEDDING_WEIGHT
            meter=AuditInferenceMeter.for_knowledge(runtime.knowledge.authenticated(token),EMBEDDING_WEIGHT)
            with meter.measure('embedding_query',1,1024) as observation:
                observation.observe_tokens(17)
        finally:
            runtime.close()
        worker_runtime = PostgreSQLRepositories.from_url(role_url(worker), pool_size=1,
            strict_role=True, runtime_role='worker')
        try:
            worker_runtime.check_schema()
            assert worker_runtime.identity.resolve(token).tenant_id == tenant
            meter=AuditInferenceMeter.for_knowledge(worker_runtime.knowledge.authenticated(token),EMBEDDING_WEIGHT)
            with meter.measure('rerank',1,1024) as observation:
                observation.observe_tokens(23)
            with migrator_engine.begin() as db:
                db.exec_driver_sql(f'GRANT SELECT (password_hash) ON public.identity_users TO "{worker}"')
            with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                worker_runtime.check_schema()
            with migrator_engine.begin() as db:
                db.exec_driver_sql(f'REVOKE SELECT (password_hash) ON public.identity_users FROM "{worker}"')
            worker_runtime.check_schema()
        finally:
            worker_runtime.close()
        for rejected_url,kind in ((role_url(app),'worker'),(role_url(worker),'api')):
            rejected = PostgreSQLRepositories.from_url(rejected_url,pool_size=1,
                strict_role=True,runtime_role=kind)
            try:
                with pytest.raises(RuntimeError,match='Unsafe PostgreSQL application role'):
                    rejected.check_schema()
            finally:
                rejected.close()
        for rejected_url in (role_url(migrator), role_url(backup), source):
            rejected = PostgreSQLRepositories.from_url(rejected_url, pool_size=1, strict_role=True)
            try:
                with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                    rejected.check_schema()
            finally:
                rejected.close()

        paths = {name:tmp_path / name for name in ('knowledge_dir','asset_dir','legacy_upload_dir')}
        for path in paths.values():
            path.mkdir()
        auth = AuthPolicy('http://127.0.0.1:8019', secure_cookie=False)
        def api_for(url):
            bundle = PostgreSQLRepositories.from_url(url, pool_size=1, strict_role=True)
            app_instance = create_app(repositories=bundle, auth_policy=auth,
                secret_store=SecretStore(path=tmp_path / 'unused-master.key'), knowledge_paths=paths)
            return bundle, app_instance

        api_bundle, api = api_for(role_url(app))
        try:
            with TestClient(api) as client:
                assert client.get('/health/live').status_code == 200
        finally:
            api_bundle.close()
        worker_api_bundle = PostgreSQLRepositories.from_url(role_url(worker),pool_size=1,
            strict_role=True,runtime_role='worker')
        try:
            with pytest.raises(ValueError,match='API database role'):
                create_app(repositories=worker_api_bundle,auth_policy=auth,
                    secret_store=SecretStore(path=tmp_path/'unused-master.key'),knowledge_paths=paths)
        finally:
            worker_api_bundle.close()
        for rejected_url in (role_url(migrator), role_url(backup), source):
            api_bundle, api = api_for(rejected_url)
            try:
                with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                    with TestClient(api):
                        pass
            finally:
                api_bundle.close()

        chat_script = ROOT / 'scripts' / 'chat_worker.py'
        knowledge_script = ROOT / 'scripts' / 'knowledge_worker.py'
        common = ['--knowledge-root', str(paths['knowledge_dir']),
            '--asset-root', str(paths['asset_dir']), '--legacy-root', str(paths['legacy_upload_dir']),
            '--once', '--require-app-role']
        for script in (chat_script, knowledge_script):
            for selected in (worker, app, migrator, backup, 'integration'):
                target_url = source if selected == 'integration' else role_url(selected)
                args = [sys.executable, str(script), '--database-url', target_url, *common]
                if script == chat_script:
                    args.extend(('--master-key-path', str(tmp_path / 'unused-master.key')))
                process = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=20)
                assert process.returncode == (0 if selected == worker else 1)
                assert 'local-role-test-only' not in (process.stdout + process.stderr)
                assert 'local-integration-only' not in (process.stdout + process.stderr)

        with backup_engine.begin() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM public.agent_runs').scalar_one() == 0
            assert db.exec_driver_sql('SELECT count(*) FROM public.cs_runtime_privilege_probe').scalar_one() == 2
            assert not db.exec_driver_sql("SELECT has_table_privilege(current_user,'public.agent_runs','INSERT')").scalar_one()
        _denied(backup_engine, "INSERT INTO public.cs_runtime_privilege_probe VALUES (2,'forbidden')")
        _denied(backup_engine, 'CREATE TABLE public.cs_backup_forbidden (id integer)')
        dump_path = tmp_path / 'isolated-data.sql'
        dump = subprocess.run([pg_dump, '-h', '::1', '-p', '55433', '-U', backup,
            '-d', database, '--data-only', '--table=public.cs_runtime_privilege_probe',
            '--no-owner', '-f', str(dump_path)],
            env=test_env, capture_output=True, text=True, timeout=30)
        assert dump.returncode == 0, dump.stderr
        assert dump_path.stat().st_size > 0

        with migrator_engine.begin() as db:
            db.exec_driver_sql(f'REVOKE SELECT ON public.agent_runs FROM "{app}"')
        _denied(app_engine, 'SELECT count(*) FROM public.agent_runs')
        with migrator_engine.begin() as db:
            db.exec_driver_sql(f'GRANT SELECT ON public.agent_runs TO "{app}"')
        with app_engine.begin() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM public.agent_runs').scalar_one() == 0
    finally:
        for engine in (app_engine, worker_engine, backup_engine, migrator_engine):
            if engine is not None:
                engine.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            if created_database:
                assert database.startswith('cs_roles_') and len(database) == 41
                db.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
            for name in reversed(created_roles):
                assert name.endswith(suffix)
                db.exec_driver_sql(f'DROP ROLE "{name}"')
        admin.dispose()
