"""Real PostgreSQL + S3 application boundaries, synthetic documents only."""
import io

import pytest
from alembic import command
from alembic.config import Config
from PIL import Image

from app.knowledge import KnowledgeError, KnowledgeService, decode_json
from app.knowledge_models import EmbeddedAsset
from app.object_store import ObjectStoreError
from app.persistence.bundle import PostgreSQLRepositories
from test_postgresql_integration import postgres_database
from test_object_store import real_s3


@pytest.fixture
def object_knowledge(postgres_database, real_s3, tmp_path, monkeypatch):
    import app.knowledge as module
    selected, client = real_s3
    command.upgrade(Config('alembic.ini'), 'head')
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    tenant = bundle.identity.create_tenant('Synthetic object tenant')
    bundle.identity.provision_user('synthetic.objects', 'synthetic-password-only', tenant, role='admin')
    token = bundle.identity.login('synthetic.objects', 'synthetic-password-only', tenant).token
    repo = bundle.knowledge.authenticated(token)
    original = module.parse_structured_document
    output = io.BytesIO()
    Image.new('RGB', (1, 1), 'white').save(output, format='PNG')
    png = output.getvalue()
    def parser(*args, **kwargs):
        doc = original(*args, **kwargs)
        doc.assets = [EmbeddedAsset(0, png, 'image/png', '.png', 'Synthetic', 'Synthetic', 1, 1)]
        return doc
    monkeypatch.setattr(module, 'parse_structured_document', parser)
    def service(instance):
        return KnowledgeService(repository=repo, object_store=selected,
            knowledge_dir=tmp_path / instance / 'docs', asset_dir=tmp_path / instance / 'assets',
            legacy_upload_dir=tmp_path / instance / 'legacy')
    try:
        yield service, repo, bundle, token, selected, client, png
    finally:
        bundle.engine.dispose()


def test_real_cross_instance_reparse_and_private_asset_without_local_files(object_knowledge):
    service, repo, bundle, token, selected, client, png = object_knowledge
    first, second = service('first'), service('second')
    result = first.ingest('synthetic.txt', b'Synthetic immutable object evidence')
    row = repo.document(result['id'])
    assert decode_json(row['metadata_json'], {})['_private_object']['version_id']
    asset = repo.assets([result['id']])[0]
    assert second.asset_content(asset['id']) == (png, 'image/png')
    assert not list(first.knowledge_dir.rglob('*.txt'))
    assert not list(first.asset_dir.rglob('*.png'))
    reparsed = second.reparse(result['id'])
    assert reparsed['id'] == result['id'] and reparsed['version'] == 2
    assert len(second.list_documents()) == 1
    assert '_private_object' not in second.list_documents()[0]['metadata']
    assert repo.document_chunks(result['id'])
    bundle.identity.logout(token)
    from app.identity import AuthenticationError
    with pytest.raises(AuthenticationError):
        second.asset_content(repo.key(repo.tables['knowledge_assets'], asset['id']))


def test_real_missing_version_and_unconfigured_store_never_use_local_fallback(object_knowledge):
    service, repo, bundle, token, selected, client, png = object_knowledge
    instance = service('first')
    result = instance.ingest('synthetic.txt', b'Synthetic missing-version evidence')
    row = repo.document(result['id'])
    ref = decode_json(row['metadata_json'], {})['_private_object']
    local = instance.knowledge_dir / row['stored_name']
    local.write_bytes(b'Synthetic must not be used as a fallback')
    client.put_object(Bucket=ref['bucket'], Key=ref['key'], Body=b'Synthetic new latest')
    client.delete_object(Bucket=ref['bucket'], Key=ref['key'], VersionId=ref['version_id'])
    with pytest.raises(ObjectStoreError):
        instance.reparse(result['id'])
    assert repo.document(result['id'])['version'] == 1
    unconfigured = KnowledgeService(repository=repo, knowledge_dir=instance.knowledge_dir.parent)
    with pytest.raises(KnowledgeError, match='对象存储未配置'):
        unconfigured.reparse(result['id'])
    asset = repo.assets([result['id']])[0]
    with pytest.raises(ObjectStoreError):
        unconfigured.asset_content(asset['id'])


def test_real_failed_publication_keeps_unreferenced_versions_for_reconciliation(object_knowledge, monkeypatch):
    service, repo, bundle, token, selected, client, png = object_knowledge
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic publication failure')
    monkeypatch.setattr(repo, 'publish_document', fail)
    with pytest.raises(RuntimeError):
        service('first').ingest('synthetic.txt', b'Synthetic failed publication evidence')
    assert repo.list_documents() == []
    assert len(client.list_object_versions(Bucket=selected.bucket).get('Versions', [])) == 2


def test_real_wrong_tenant_asset_denied_before_object_read(object_knowledge, monkeypatch, tmp_path):
    service, repo, bundle, token, selected, client, png = object_knowledge
    result = service('first').ingest('synthetic.txt', b'Synthetic tenant-separated evidence')
    asset = repo.assets([result['id']])[0]
    tenant = bundle.identity.create_tenant('Synthetic foreign tenant')
    bundle.identity.provision_user('synthetic.foreign', 'synthetic-password-only', tenant, role='admin')
    foreign = bundle.identity.login('synthetic.foreign', 'synthetic-password-only', tenant).token
    instance = KnowledgeService(repository=bundle.knowledge.authenticated(foreign), object_store=selected,
        knowledge_dir=tmp_path / 'foreign-docs', asset_dir=tmp_path / 'foreign-assets')
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized object read')
    monkeypatch.setattr(selected, 'get', forbidden)
    with pytest.raises(KnowledgeError):
        instance.asset_content(asset['id'])
    with pytest.raises(KnowledgeError):
        instance.reparse(result['id'])


def test_real_object_documents_refuse_local_only_backup(object_knowledge):
    from app.backup import _references, BackupError
    service, repo, bundle, token, selected, client, png = object_knowledge
    service('first').ingest('synthetic.txt', b'Synthetic remote backup boundary evidence')
    with bundle.engine.connect() as db, pytest.raises(BackupError, match='local-cache fallback'):
        _references(db)


def test_real_object_asset_http_and_mid_read_revocation(object_knowledge, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.auth_http import AuthPolicy
    from app.main import create_app
    from app.security import SecretStore
    service, repo, bundle, token, selected, remote, png = object_knowledge
    result = service('first').ingest('synthetic.txt', b'Synthetic HTTP object evidence')
    asset = repo.assets([result['id']])[0]
    tenant = repo.storage_namespace()
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle, auth_policy=policy,
        secret_store=SecretStore(path=tmp_path / 'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path / 'http-docs', 'asset_dir':tmp_path / 'http-assets',
                         'object_store':selected})
    with TestClient(app, base_url=policy.origin, client=('127.0.0.1', 50000)) as http:
        login = http.post('/api/auth/login', headers={'Origin':policy.origin},
            json={'username':'synthetic.objects', 'password':'synthetic-password-only', 'tenant_id':tenant})
        assert login.status_code == 200
        url = f"/api/knowledge/assets/{asset['id']}"
        response = http.get(url)
        assert response.status_code == 200 and response.content == png
        assert response.headers['cache-control'] == 'no-store'
        assert '_private_object' not in http.get(f"/api/admin/documents/{result['id']}").json()['document']['metadata']
        original = selected.get
        def unavailable(*args, **kwargs):
            raise ObjectStoreError('Synthetic unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(selected, 'get', unavailable)
            assert http.get(url).status_code == 503
            headers = {'Origin':policy.origin, 'X-CSRF-Token':login.json()['csrf_token']}
            failed = http.post(f"/api/admin/documents/{result['id']}/reparse", headers=headers)
            assert failed.status_code == 503 and failed.json()['code'] == 'object_store_unavailable'
        def revoked(*args, **kwargs):
            data = original(*args, **kwargs)
            bundle.identity.logout(http.cookies.get(policy.cookie_name))
            return data
        monkeypatch.setattr(selected, 'get', revoked)
        assert http.get(url).status_code == 401
    assert not (tmp_path / 'unused.key').exists()
