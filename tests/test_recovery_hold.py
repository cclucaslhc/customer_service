"""Real PostgreSQL recovery fencing, HTTP and independent process acceptance."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import uuid

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from app.auth_http import AuthPolicy
from app.backup import BackupError, create_backup, restore_backup
from app.main import create_app
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.durable_chat_repository import DurableChatRepository
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
from app.persistence.engine import build_engine
from app.persistence.schema import metadata
from app.recovery import RecoveryHold
from app.security import SecretStore
from test_postgresql_integration import postgres_database
from test_logical_backup import backup_setup, empty_database


def test_restored_pending_run_cannot_be_claimed_by_any_worker(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    session_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    DurableChatRepository(bundle.engine).authenticated(token).enqueue(session_id=session_id, request_id=run_id,
        content='Synthetic queued recovery question', title='Synthetic recovery', provider='deepseek',
        model='synthetic', prompt_version_id=None, instructions='Synthetic only')
    create_backup(**common, destination=package, maintenance_confirmed=True)
    with empty_database() as restored_url:
        options = {**common, 'url': restored_url, 'knowledge_root': tmp_path / 'held-knowledge',
                   'asset_root': tmp_path / 'held-assets'}
        assert restore_backup(**options, source=package, isolated_confirmed=True)['recovery_hold']
        restored = PostgreSQLRepositories.from_url(restored_url, pool_size=2)
        try:
            for action in (restored.check_schema, restored.initialize,
                           DurableChatRepository(restored.engine).claim_next,
                           DurableKnowledgeRepository(restored.engine).claim_next):
                with pytest.raises(RecoveryHold):
                    action()
            commands = (
                [sys.executable, 'scripts/chat_worker.py', '--database-url', restored_url,
                 '--master-key-path', str(tmp_path / 'unused.key'), '--synthetic', '--once',
                 '--knowledge-root', str(options['knowledge_root']), '--asset-root', str(options['asset_root']),
                 '--legacy-root', str(tmp_path / 'held-legacy')],
                [sys.executable, 'scripts/knowledge_worker.py', '--database-url', restored_url, '--once',
                 '--knowledge-root', str(options['knowledge_root']), '--asset-root', str(options['asset_root']),
                 '--legacy-root', str(tmp_path / 'held-legacy')],
            )
            for args in commands:
                result = subprocess.run(args, capture_output=True, text=True, timeout=20)
                assert result.returncode == 1 and 'RecoveryHold' in result.stderr
            with restored.engine.connect() as db:
                runs = metadata.tables['agent_runs']
                row = db.execute(sa.select(runs).where(runs.c.id == uuid.UUID(run_id))).mappings().one()
                assert row['status'] == 'queued' and row['lease_fence'] == 0 and row['lease_owner'] is None
            assert not (tmp_path / 'unused.key').exists()
        finally:
            restored.close()


def test_running_http_instance_fails_closed_when_recovery_hold_appears(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    policy = AuthPolicy('https://localhost')
    application = create_app(repositories=bundle, auth_policy=policy,
        secret_store=SecretStore(path=tmp_path / 'unused.key'),
        knowledge_paths={'knowledge_dir': common['knowledge_root'], 'asset_dir': common['asset_root'],
                         'legacy_upload_dir': tmp_path / 'legacy'}, durable_chat=True)
    with TestClient(application, base_url=policy.origin) as client:
        assert client.get('/api/auth/config').status_code == 200
        with bundle.engine.begin() as db:
            db.execute(metadata.tables['recovery_state'].insert().values(id=1, blocked=True, backup_id=uuid.uuid4(),
                                                                        restored_at=datetime.now(timezone.utc)))
        for path in ('/api/auth/config', '/api/health', '/api/chat/sessions', '/admin/knowledge'):
            response = client.get(path)
            assert response.status_code == 503 and response.json()['code'] == 'recovery_hold'
            assert response.headers['cache-control'] == 'no-store'
        assert client.post('/api/auth/logout').status_code == 503
    with pytest.raises(RecoveryHold):
        with TestClient(application, base_url=policy.origin):
            pass
    assert not (tmp_path / 'unused.key').exists()


def test_hold_insert_failure_rolls_back_whole_restore_transaction(backup_setup, tmp_path, monkeypatch):
    bundle, common, package, tenant, token, revoked = backup_setup
    create_backup(**common, destination=package, maintenance_confirmed=True)
    import app.backup as backup_module
    original_command = backup_module._command
    def fail_hold(binary_dir, name, url, arguments):
        if name == 'psql':
            hold_path = Path(next(value[len('--file='):] for value in reversed(arguments) if value.startswith('--file=')))
            hold_path.write_text('INSERT INTO public.recovery_state(id,blocked,backup_id,restored_at) '
                "VALUES(2,true,'00000000-0000-0000-0000-000000000001',clock_timestamp());\n", encoding='ascii')
        return original_command(binary_dir, name, url, arguments)
    monkeypatch.setattr(backup_module, '_command', fail_hold)
    with empty_database() as restored_url:
        options = {**common, 'url': restored_url, 'knowledge_root': tmp_path / 'failed-hold-knowledge',
                   'asset_root': tmp_path / 'failed-hold-assets'}
        with pytest.raises(BackupError):
            restore_backup(**options, source=package, isolated_confirmed=True)
        engine = build_engine(restored_url, pool_size=1)
        try:
            with engine.connect() as db:
                assert not sa.inspect(db).get_table_names(schema='public')
        finally:
            engine.dispose()
        # Files are retained for diagnosis after SQL has begun, not wiped on error.
        assert options['knowledge_root'].exists() and options['asset_root'].exists()


def test_upgrade_0013_adds_empty_recovery_state_without_blocking_fresh_database(postgres_database):
    config = Config('alembic.ini')
    command.upgrade(config, '0013_knowledge_queue')
    command.upgrade(config, 'head')
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=1)
    try:
        bundle.check_schema()
        with bundle.engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['recovery_state'])).scalar_one() == 0
    finally:
        bundle.close()
