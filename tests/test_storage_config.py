"""Explicit storage configuration on real isolated PG/S3; no actual service keys."""
import os
import json
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
import uuid
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from app.auth_http import AuthPolicy
from app.identity import AuthorizationError, AuthenticationError
from app.main import create_app
from app.object_store import ObjectStoreError
from app.security import SecretStore
from app.storage_config import StorageConfiguration, StoragePolicy, StorageTarget, StorageConflict
from app.persistence.schema import metadata
from app.persistence.bundle import PostgreSQLRepositories
from app.knowledge import KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_worker import KnowledgeWorker
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database
from test_object_store import real_s3
from test_object_backup import restore_store
from test_logical_backup import empty_database


@pytest.fixture
def configured_storage(queue_setup, real_s3, tmp_path):
    bundle, _, _, tokens = queue_setup
    store, client = real_s3
    policy = StoragePolicy([StorageTarget('approved','http://127.0.0.1:59000',store.bucket,'us-east-1')],loopback_tests=True)
    secrets = SecretStore(path=tmp_path/'wrapping'/'storage.key')
    manager = StorageConfiguration(bundle.engine,secrets,policy)
    return bundle,tokens,store,client,secrets,policy,manager


def save(manager, token, revision=0, target='approved'):
    return manager.save(token,target_name=target,expected_revision=revision,
        access_key='synthetic-s3-access',secret_key='synthetic-s3-secret-only')


def test_real_config_permission_encryption_rotation_and_atomic_audit(configured_storage):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    admin = tokens['synthetic.admin']
    assert not manager.describe(admin)['configured'] and not secrets.path.exists()
    with pytest.raises(AuthorizationError):
        save(manager,tokens['synthetic.customer'])
    assert not secrets.path.exists()
    assert save(manager,admin)['revision'] == 1
    assert 'synthetic-s3-secret-only' not in str(manager.describe(admin))
    with bundle.engine.connect() as db:
        row = db.execute(sa.select(metadata.tables['storage_configs'])).mappings().one()
        assert b'synthetic-s3-secret-only' not in row['encrypted_credentials']
        assert len(row['nonce']) == 12
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.action == 'storage.configure')).scalar_one() == 1
    assert save(manager,admin,1)['revision'] == 2
    with pytest.raises(StorageConflict):
        save(manager,admin,1)
    resolved = manager.resolve(tokens['synthetic.customer'])
    assert resolved.bucket == store.bucket
    resolved.close()


def test_real_changed_deployment_target_rejected_before_decryption(configured_storage, monkeypatch):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    save(manager,tokens['synthetic.admin'])
    changed = StoragePolicy([StorageTarget('approved','http://127.0.0.1:59000','different-private','us-east-1')],loopback_tests=True)
    monkeypatch.setattr(secrets,'decrypt',lambda *args,**kwargs:pytest.fail('Unapproved binding was decrypted'))
    with pytest.raises(ObjectStoreError,match='target changed'):
        StorageConfiguration(bundle.engine,secrets,changed).resolve(tokens['synthetic.admin'])
    with pytest.raises(ObjectStoreError,match='policy required'):
        StorageConfiguration(bundle.engine,secrets,None).resolve(tokens['synthetic.admin'])


def test_real_active_target_change_rejected_before_network(configured_storage, monkeypatch):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    save(manager,tokens['synthetic.admin'])
    targets = StoragePolicy([*policy.targets.values(),StorageTarget('other',
        'http://127.0.0.1:59000','different-private','us-east-1')],loopback_tests=True)
    changed = StorageConfiguration(bundle.engine,secrets,targets)
    monkeypatch.setattr(targets,'build',lambda *args:pytest.fail('Target change reached network'))
    with pytest.raises(StorageConflict,match='separate referenced-object migration'):
        save(changed,tokens['synthetic.admin'],1,target='other')
    assert manager.describe(tokens['synthetic.admin'])['revision'] == 1


def test_real_concurrent_rotation_has_one_winner(configured_storage, monkeypatch):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    save(manager,tokens['synthetic.admin'])
    barrier = threading.Barrier(2)
    original = policy.build
    def build(*args):
        sdk = original(*args)
        check = sdk.check_ready
        def ready():
            check()
            barrier.wait(timeout=10)
        sdk.check_ready = ready
        return sdk
    monkeypatch.setattr(policy,'build',build)
    def rotate():
        try:
            return save(manager,tokens['synthetic.admin'],1)['revision']
        except StorageConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _:rotate(),range(2)))
    assert sorted(outcomes,key=str) == [2,'conflict']
    assert manager.describe(tokens['synthetic.admin'])['revision'] == 2
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.action == 'storage.configure')).scalar_one() == 2


def test_policy_loader_is_explicit_bounded_and_production_tls_only(tmp_path):
    policy_file = tmp_path/'approved.json'
    row = {'name':'approved','endpoint':'https://objects.example.invalid',
        'bucket':'synthetic-private','region':'us-east-1'}
    policy_file.write_text(json.dumps([row]),encoding='utf-8')
    assert StoragePolicy.from_file(policy_file).target('approved').bucket == 'synthetic-private'
    with pytest.raises(ValueError):
        StoragePolicy.from_file(Path('relative-policy.json'))
    for value in ([],[row]*33,[row,row],[{**row,'endpoint':'http://127.0.0.1:59000'}],
                  [{**row,'endpoint':'https://user:password@objects.example.invalid'}],
                  [{**row,'endpoint':'https://169.254.169.254'}]):
        policy_file.write_text(json.dumps(value),encoding='utf-8')
        with pytest.raises(ValueError):
            StoragePolicy.from_file(policy_file)
    policy_file.write_text(' '*16385,encoding='utf-8')
    with pytest.raises(ValueError):
        StoragePolicy.from_file(policy_file)


def test_real_foreign_ciphertext_cannot_be_replayed_into_another_tenant(configured_storage):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    save(manager,tokens['synthetic.admin'])
    tenant = bundle.identity.create_tenant('Synthetic other storage tenant')
    bundle.identity.provision_user('synthetic.storage.other','synthetic-password-only',tenant,role='admin')
    token = bundle.identity.login('synthetic.storage.other','synthetic-password-only',tenant).token
    assert not manager.describe(token)['configured'] and manager.resolve(token) is None
    table = metadata.tables['storage_configs']
    with bundle.engine.begin() as db:
        row = dict(db.execute(sa.select(table)).mappings().one())
        row['tenant_id'] = uuid.UUID(tenant)
        db.execute(table.insert().values(**row))
    with pytest.raises(ObjectStoreError,match='binding invalid'):
        manager.resolve(token)


def test_real_revocation_during_readiness_does_not_create_wrapping_key(configured_storage, monkeypatch):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    original = policy.build
    def build(*args):
        sdk = original(*args)
        check = sdk.check_ready
        def revoked():
            check()
            bundle.identity.logout(tokens['synthetic.admin'])
        sdk.check_ready = revoked
        return sdk
    monkeypatch.setattr(policy,'build',build)
    with pytest.raises(AuthenticationError):
        save(manager,tokens['synthetic.admin'])
    assert not secrets.path.exists()
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['storage_configs'])).scalar_one() == 0


def test_real_config_audit_failure_rolls_back_credentials(configured_storage, monkeypatch):
    bundle,tokens,store,client,secrets,policy,manager = configured_storage
    save(manager,tokens['synthetic.admin'])
    from sqlalchemy import event
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO audit_logs'):
            raise RuntimeError('Synthetic audit write failure')
    event.listen(bundle.engine,'before_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):
            save(manager,tokens['synthetic.admin'],1)
    finally:
        event.remove(bundle.engine,'before_cursor_execute',fail)
    assert manager.describe(tokens['synthetic.admin'])['revision'] == 1


def test_real_http_save_and_worker_consume_config_without_service_keys_in_cli(configured_storage, tmp_path, postgres_database):
    bundle,tokens,store,remote,secrets,policy,manager = configured_storage
    auth = AuthPolicy('https://localhost')
    tenant = bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    paths = {'knowledge_dir':tmp_path/'docs','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'}
    app = create_app(repositories=bundle,secret_store=secrets,auth_policy=auth,storage_policy=policy,
        knowledge_paths=paths,durable_knowledge=True)
    with TestClient(app,base_url=auth.origin,client=('127.0.0.1',50000)) as http:
        login = http.post('/api/auth/login',headers={'Origin':auth.origin},json={
            'username':'synthetic.admin','password':'synthetic-password-only','tenant_id':tenant})
        assert login.status_code == 200
        headers = {'Origin':auth.origin,'X-CSRF-Token':login.json()['csrf_token']}
        payload = {'target':'approved','revision':0,'access_key':'synthetic-s3-access','secret_key':'synthetic-s3-secret-only'}
        assert http.put('/api/admin/storage',json=payload,headers={'Origin':auth.origin}).status_code == 403
        response = http.put('/api/admin/storage',json=payload,headers=headers)
        assert response.status_code == 200 and response.json()['revision'] == 1
        assert 'synthetic-s3-secret-only' not in response.text
        invalid = http.put('/api/admin/storage',json={**payload,'revision':'synthetic-secret-only'},headers=headers)
        assert invalid.status_code == 422
        assert 'synthetic-secret-only' not in invalid.text and 'synthetic-s3-secret-only' not in invalid.text
        managed = paths['knowledge_dir']/tenant/'input'
        managed.mkdir(parents=True)
        (managed/'synthetic.txt').write_bytes(b'Synthetic stored-config parser input')
        job = http.post('/api/admin/knowledge/jobs',json={'path':str(managed),'recursive':True},headers=headers)
        assert job.status_code == 202, job.text
        (managed/'synthetic.txt').unlink()
        import subprocess, sys
        result = subprocess.run([sys.executable,'tests/fixtures/object_knowledge_worker.py',postgres_database,
            store.bucket,str(tmp_path/'worker'),str(secrets.path)],capture_output=True,text=True,timeout=30)
        assert result.returncode == 0, result.stderr
        state = http.get('/api/admin/knowledge/jobs/'+job.json()['id']).json()
        assert state['status'] == 'complete' and state['imported_count'] == 1


def test_real_restore_fences_old_config_and_requires_matching_rebind(configured_storage, postgres_database, tmp_path):
    from app.backup import create_backup, restore_backup
    bundle,tokens,source,client,secrets,policy,manager = configured_storage
    admin = tokens['synthetic.admin']
    tenant = bundle.identity.resolve(admin).tenant_id
    save(manager,admin)
    active = manager.resolve(admin)
    docs,assets = tmp_path/'docs',tmp_path/'assets'
    KnowledgeService(repository=bundle.knowledge.authenticated(admin),object_store=active,
        knowledge_dir=docs,asset_dir=assets).ingest('synthetic.txt',b'Synthetic storage recovery evidence')
    assets.mkdir(exist_ok=True)
    active.close()
    binaries = os.environ.get('TEST_POSTGRES_BIN')
    if not binaries:
        pytest.skip('TEST_POSTGRES_BIN unset; config restore unverified')
    key_dir = tmp_path/'backup-keys'; key_dir.mkdir()
    key = key_dir/'key'; key.write_bytes(os.urandom(32))
    package_dir = tmp_path/'packages'; package_dir.mkdir()
    package = package_dir/'backup'
    common = {'url':postgres_database,'binary_dir':Path(binaries),'knowledge_root':docs,'asset_root':assets,
        'scratch_root':tmp_path,'key_path':key}
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with empty_database() as url, restore_store(client) as target:
        report = restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
            'asset_root':tmp_path/'restored-assets'},source=package,isolated_confirmed=True,object_store=target)
        assert report['storage_rebind_required'] == 1
        restored = PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            new_policy = StoragePolicy([StorageTarget('restored','http://127.0.0.1:59000',target.bucket,'us-east-1'),
                *policy.targets.values()],loopback_tests=True)
            recovery = StorageConfiguration(restored.engine,secrets,new_policy)
            # Synthetic maintenance releases only this disposable hold, never app/API behavior.
            with restored.engine.begin() as db:
                db.execute(metadata.tables['recovery_state'].update().values(blocked=False))
            assert recovery.describe(admin)['recovery_rebind']
            with pytest.raises(ObjectStoreError,match='rebind'):
                recovery.resolve(admin)
            with pytest.raises(StorageConflict,match='does not match restored'):
                save(recovery,admin,1,target='approved')
            assert recovery.describe(admin)['recovery_rebind']
            save(recovery,admin,1,target='restored')
            sdk = recovery.resolve(admin)
            assert sdk.bucket == target.bucket
            sdk.close()
        finally:
            restored.close()
