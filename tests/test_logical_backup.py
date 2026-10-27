"""Encrypted files and actual pg_dump/pg_restore on disposable databases only."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import uuid
import subprocess
import sys

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.backup import (BackupError, _encrypt, _decrypt, _file, _validated_url, checked_directory,
                        create_backup, restore_backup, database_manifest)
from app.persistence.engine import build_engine
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.schema import metadata, SCHEMA_REVISION
from test_postgresql_integration import postgres_database


def test_encryption_authenticates_bytes_key_and_identity(tmp_path):
    key = os.urandom(32)
    source = tmp_path / 'synthetic.txt'
    source.write_bytes(b'Synthetic customer backup only')
    encrypted = tmp_path / 'file.enc'
    assert _encrypt(source, encrypted, key, 'file-0')['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert b'Synthetic customer' not in encrypted.read_bytes()
    restored = tmp_path / 'restored.txt'
    _decrypt(encrypted, restored, key, 'file-0')
    assert restored.read_bytes() == source.read_bytes()
    for selected_key, label in ((os.urandom(32), 'file-0'), (key, 'file-1')):
        with pytest.raises(BackupError):
            _decrypt(encrypted, tmp_path / 'invalid.txt', selected_key, label)
        assert not (tmp_path / 'invalid.txt').exists()
    data = bytearray(encrypted.read_bytes())
    data[-17] ^= 1
    encrypted.write_bytes(data)
    with pytest.raises(BackupError):
        _decrypt(encrypted, tmp_path / 'invalid.txt', key, 'file-0')
    assert not (tmp_path / 'invalid.txt').exists()


@pytest.mark.parametrize('name', ('../escape', '/absolute', 'C:/escape', 'folder\\escape', 'folder//file', './file'))
def test_manifest_paths_reject_escape(tmp_path, name):
    with pytest.raises(BackupError):
        _file(tmp_path, name)


def test_backups_reject_project_data_and_implicit_paths(tmp_path):
    with pytest.raises(BackupError):
        checked_directory(Path('relative'))
    with pytest.raises(BackupError):
        checked_directory(Path(__file__).resolve().parents[1] / 'data' / 'backup')
    with pytest.raises(BackupError):
        checked_directory(tmp_path / 'secrets')
    with pytest.raises(BackupError):
        checked_directory(Path(tmp_path.anchor))


@pytest.mark.parametrize('url', (
    'postgresql+psycopg://integration:synthetic@remote.invalid/example',
    'postgresql+psycopg://integration:synthetic@127.0.0.1/example?options=unsafe',
    'sqlite:///example.sqlite',
))
def test_backup_refuses_remote_or_implicit_connection_options(url):
    with pytest.raises(BackupError):
        _validated_url(url)


def test_backup_requires_matching_postgresql_client_major(monkeypatch, tmp_path):
    import app.backup as backup_module
    from types import SimpleNamespace

    class Database:
        def exec_driver_sql(self, statement):
            assert statement == 'SHOW server_version_num'
            return SimpleNamespace(scalar_one=lambda: '160015')

    calls = []
    monkeypatch.setattr(backup_module, '_tool', lambda directory, name: name)

    def version(command, **kwargs):
        calls.append(command[0])
        major = '17.11' if command[0] == 'pg_restore' else '16.15'
        return SimpleNamespace(returncode=0,
            stdout=f'{command[0]} (PostgreSQL) {major}'.encode(), stderr=b'')

    monkeypatch.setattr(backup_module.subprocess, 'run', version)
    with pytest.raises(BackupError, match='major versions must match'):
        backup_module._require_matching_client_major(Database(), tmp_path)
    assert calls == ['pg_dump', 'pg_restore']

    calls.clear()
    monkeypatch.setattr(backup_module.subprocess, 'run', lambda command, **kwargs:
        SimpleNamespace(returncode=0,
            stdout=f'{command[0]} (PostgreSQL) 16.15'.encode(), stderr=b''))
    backup_module._require_matching_client_major(Database(), tmp_path)


@contextmanager
def empty_database():
    admin_url = os.environ['TEST_POSTGRES_URL']
    parsed = make_url(admin_url)
    assert parsed.host in {'localhost', '127.0.0.1', '::1'}
    name = 'cs_test_' + uuid.uuid4().hex
    engine = build_engine(admin_url, pool_size=1)
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
        db.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        yield parsed.set(database=name).render_as_string(hide_password=False)
    finally:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            db.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        engine.dispose()


@pytest.fixture
def backup_setup(postgres_database, tmp_path):
    binaries = os.environ.get('TEST_POSTGRES_BIN')
    if not binaries:
        pytest.skip('TEST_POSTGRES_BIN unset; real backup/restore remains unverified')
    command.upgrade(Config('alembic.ini'), 'head')
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    try:
        tenant = bundle.identity.create_tenant('Synthetic backup tenant')
        bundle.identity.provision_user('synthetic.backup', 'synthetic-backup-only', tenant, role='admin')
        token = bundle.identity.login('synthetic.backup', 'synthetic-backup-only', tenant).token
        revoked = bundle.identity.login('synthetic.backup', 'synthetic-backup-only', tenant).token
        bundle.identity.logout(revoked)
        from app.database import utc_now
        bundle.config.authenticated(token).initialize_tenant(utc_now())
        with bundle.engine.begin() as db:
            providers = metadata.tables['provider_configs']
            db.execute(providers.update().where(providers.c.tenant_id == uuid.UUID(tenant), providers.c.provider == 'deepseek')
                .values(encrypted_key=b'synthetic-nonfunctional-ciphertext', nonce=b'synthetic-12', key_aad_version=1))
        from app.knowledge import KnowledgeService
        knowledge_root, asset_root = tmp_path / 'knowledge', tmp_path / 'assets'
        knowledge = KnowledgeService(repository=bundle.knowledge.authenticated(token),
            knowledge_dir=knowledge_root, asset_dir=asset_root, legacy_upload_dir=tmp_path / 'legacy')
        knowledge.ingest('synthetic.txt', b'Synthetic retained knowledge for backup.')
        asset_root.mkdir(exist_ok=True)
        # One real synthetic image stored with its actual metadata for file verification.
        from PIL import Image
        from io import BytesIO
        image = BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(image, format='PNG')
        data = image.getvalue()
        image_name = 'synthetic-image.png'
        (asset_root / tenant).mkdir(parents=True, exist_ok=True)
        (asset_root / tenant / image_name).write_bytes(data)
        with bundle.engine.begin() as db:
            document = db.execute(sa.select(metadata.tables['documents'].c.id)).scalar_one()
            db.execute(metadata.tables['knowledge_assets'].insert().values(id=uuid.uuid4(), document_id=document,
                tenant_id=uuid.UUID(tenant), asset_index=0, stored_name=image_name, media_type='image/png',
                size=len(data), width=20, height=20, location='synthetic', alt_text='synthetic',
                sha256=hashlib.sha256(data).hexdigest(), metadata_json={}, created_at=utc_now()))
        keys = tmp_path / 'keys'
        keys.mkdir()
        key_path = keys / 'backup.key'
        key_path.write_bytes(os.urandom(32))
        packages = tmp_path / 'packages'
        packages.mkdir()
        common = dict(url=postgres_database, binary_dir=Path(binaries), knowledge_root=knowledge_root,
                      asset_root=asset_root, key_path=key_path, scratch_root=tmp_path)
        yield bundle, common, packages / 'backup', tenant, token, revoked
    finally:
        bundle.close()


def test_real_encrypted_logical_backup_restore_and_identity(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    with pytest.raises(BackupError, match='maintenance'):
        create_backup(**common, destination=package)
    result = create_backup(**common, destination=package, maintenance_confirmed=True)
    assert result['files'] == 2 and result['schema'] == SCHEMA_REVISION
    assert set(p.name for p in package.iterdir()) == {'database.enc', 'manifest.enc', 'file-0.enc', 'file-1.enc', 'READY'}
    with pytest.raises(BackupError):
        create_backup(**common, destination=package, maintenance_confirmed=True)
    with empty_database() as restored_url:
        options = {**common, 'url': restored_url, 'knowledge_root': tmp_path / 'restored-knowledge',
                   'asset_root': tmp_path / 'restored-assets'}
        report = restore_backup(**options, source=package, isolated_confirmed=True)
        assert report['verified'] and not report['workers_enabled']
        restored = PostgreSQLRepositories.from_url(restored_url, pool_size=2)
        try:
            # Original snapshot verified by restore before deliberate path relocation.
            with bundle.engine.connect() as original, restored.engine.connect() as target:
                before, after = database_manifest(original), database_manifest(target)
                assert before['schema'] == after['schema'] and before['sequences'] == after['sequences']
                assert {k: v for k, v in before['tables'].items() if k not in {'documents','recovery_state'}} == {
                    k: v for k, v in after['tables'].items() if k not in {'documents','recovery_state'}}
                assert before['tables']['documents']['rows'] == after['tables']['documents']['rows']
            assert restored.identity.resolve(token).tenant_id == tenant
            restored.identity.logout(token)
            from app.identity import AuthenticationError
            with pytest.raises(AuthenticationError):
                restored.identity.resolve(revoked)
            with pytest.raises(AuthenticationError):
                restored.identity.resolve(token)
            from app.knowledge import KnowledgeService
            replacement = restored.identity.login('synthetic.backup', 'synthetic-backup-only', tenant).token
            knowledge = KnowledgeService(repository=restored.knowledge.authenticated(replacement),
                knowledge_dir=options['knowledge_root'], asset_dir=options['asset_root'], legacy_upload_dir=tmp_path / 'restored-legacy')
            assert knowledge.search('retained knowledge')
            document = knowledge.list_documents()[0]
            asset = knowledge.list_assets(document['id'])[0]
            restored_image, media_type = knowledge.asset_file(asset['id'])
            assert media_type == 'image/png' and restored_image.read_bytes().startswith(b'\x89PNG')
            from app.recovery import RecoveryHold
            with pytest.raises(RecoveryHold):
                restored.check_schema()
            with pytest.raises(RecoveryHold):
                knowledge.reparse(document['id'])
        finally:
            restored.close()


def test_changed_referenced_file_aborts_and_preserves_originals(backup_setup):
    bundle, common, package, tenant, token, revoked = backup_setup
    source = next((common['knowledge_root'] / tenant).rglob('*.txt'))
    source.write_bytes(b'Synthetic modified input')
    with pytest.raises(BackupError, match='changed'):
        create_backup(**common, destination=package, maintenance_confirmed=True)
    assert not package.exists()
    assert source.read_bytes() == b'Synthetic modified input'


def test_tampered_backup_never_runs_sql_and_removes_owned_staging(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    create_backup(**common, destination=package, maintenance_confirmed=True)
    encrypted = package / 'file-0.enc'
    data = bytearray(encrypted.read_bytes())
    data[-1] ^= 1
    encrypted.write_bytes(data)
    with empty_database() as restored_url:
        options = {**common, 'url': restored_url, 'knowledge_root': tmp_path / 'bad-knowledge',
                   'asset_root': tmp_path / 'bad-assets'}
        with pytest.raises(BackupError, match='authentication'):
            restore_backup(**options, source=package, isolated_confirmed=True)
        assert not options['knowledge_root'].exists() and not options['asset_root'].exists()
        engine = build_engine(restored_url, pool_size=1)
        try:
            with engine.connect() as db:
                assert not sa.inspect(db).get_table_names()
        finally:
            engine.dispose()


def test_restore_refuses_nonempty_database_and_existing_roots(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    create_backup(**common, destination=package, maintenance_confirmed=True)
    with pytest.raises(BackupError, match='roots'):
        restore_backup(**common, source=package, isolated_confirmed=True)
    options = {**common, 'knowledge_root': tmp_path / 'new-knowledge', 'asset_root': tmp_path / 'new-assets'}
    with pytest.raises(BackupError, match='empty'):
        restore_backup(**options, source=package, isolated_confirmed=True)
    assert not options['knowledge_root'].exists()


def test_real_cli_create_and_isolated_restore_reports_no_secrets(backup_setup, tmp_path):
    bundle, common, package, tenant, token, revoked = backup_setup
    def run(operation, url, knowledge, assets, confirmation):
        env = dict(os.environ, BACKUP_DATABASE_URL=url)
        result = subprocess.run([sys.executable, 'scripts/logical_backup.py', operation,
            '--binary-dir', str(common['binary_dir']), '--package', str(package),
            '--knowledge-root', str(knowledge), '--asset-root', str(assets),
            '--key-path', str(common['key_path']), '--scratch-root', str(common['scratch_root']), confirmation],
            env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert token not in result.stdout and revoked not in result.stdout
        assert 'local-integration-only' not in result.stdout and 'synthetic-nonfunctional' not in result.stdout
        return json.loads(result.stdout)
    backup = run('create', common['url'], common['knowledge_root'], common['asset_root'], '--confirm-offline-maintenance')
    with empty_database() as restored_url:
        restored = run('restore', restored_url, tmp_path / 'cli-knowledge', tmp_path / 'cli-assets',
                       '--confirm-isolated-empty-restore')
    assert backup['backup_id'] == restored['backup_id'] and backup['snapshot_at'] == restored['snapshot_at']
    assert restored['verified'] and restored['activation_requires_reconciliation'] and restored['elapsed_seconds'] > 0
    print(json.dumps({'backup_id': backup['backup_id'], 'schema': restored['schema'], 'files': restored['files'],
        'backup_seconds': backup['elapsed_seconds'], 'restore_verify_seconds': restored['elapsed_seconds'],
        'verified': restored['verified'], 'workers_enabled': restored['workers_enabled']}))


def test_pg_dump_and_semantic_manifest_share_exported_snapshot(backup_setup, tmp_path, monkeypatch):
    bundle, common, package, tenant, token, revoked = backup_setup
    import app.backup as backup_module
    original_command = backup_module._command
    def concurrent_commit(binary_dir, name, url, arguments):
        if name == 'pg_dump':
            with bundle.engine.begin() as db:
                table = metadata.tables['app_settings']
                db.execute(table.insert().values(tenant_id=uuid.UUID(tenant), key='synthetic_after_snapshot', value='synthetic'))
        return original_command(binary_dir, name, url, arguments)
    monkeypatch.setattr(backup_module, '_command', concurrent_commit)
    create_backup(**common, destination=package, maintenance_confirmed=True)
    with empty_database() as restored_url:
        options = {**common, 'url': restored_url, 'knowledge_root': tmp_path / 'snapshot-knowledge',
                   'asset_root': tmp_path / 'snapshot-assets'}
        assert restore_backup(**options, source=package, isolated_confirmed=True)['verified']
        engine = build_engine(restored_url, pool_size=1)
        try:
            with engine.connect() as db:
                table = metadata.tables['app_settings']
                assert db.execute(sa.select(table).where(table.c.key == 'synthetic_after_snapshot')).first() is None
        finally:
            engine.dispose()
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(table).where(table.c.key == 'synthetic_after_snapshot')).first() is not None
