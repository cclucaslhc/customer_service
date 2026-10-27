"""Authenticated upload/reparse receipts execute only through the durable queue."""
from fastapi.testclient import TestClient
import pytest
from app.auth_http import AuthPolicy
from app.isolated_parser import IsolatedDocumentParser
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_worker import KnowledgeWorker
from app.main import create_app
from app.security import SecretStore
from test_durable_chat import queue_setup
from test_durable_knowledge import setup
from test_postgresql_integration import postgres_database
from test_logical_backup import backup_setup,empty_database


def test_http_upload_and_reparse_return_tasks_and_never_parse_in_api(queue_setup,tmp_path,monkeypatch):
    import app.knowledge as knowledge_module
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    tenant = bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle,auth_policy=policy,durable_knowledge=True,
        secret_store=SecretStore(path=tmp_path/'unused.key'),knowledge_paths=paths)
    def no_api_parser(*args,**kwargs):
        raise AssertionError('HTTP must enqueue instead of parsing')
    monkeypatch.setattr(knowledge_module,'parse_structured_document',no_api_parser)
    worker = KnowledgeWorker(bundle,paths,document_parser=IsolatedDocumentParser(timeout_seconds=30))
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        response = client.post('/api/auth/login',headers={'Origin':policy.origin},json={
            'username':'synthetic.admin','password':'synthetic-password-only','tenant_id':tenant})
        headers = {'Origin':policy.origin,'X-CSRF-Token':response.json()['csrf_token']}
        assert client.post('/api/admin/documents/not-an-id/reparse',headers=headers).status_code == 400
        uploaded = client.post('/api/admin/documents',headers=headers,files={
            'file':('合成上传.txt','Synthetic queued upload evidence.'.encode(),'text/plain')})
        assert uploaded.status_code == 202 and uploaded.json()['status'] == 'pending'
        assert not any(key in uploaded.json() for key in ('lease_owner','lease_fence','execution_slot_held'))
        assert service.list_documents() == []
        assert worker.run_once()
        job = client.get('/api/admin/knowledge/jobs/'+uploaded.json()['id']).json()
        assert job['status'] == 'complete' and job['imported_count'] == 1
        document = service.list_documents()[0]
        assert document['display_name'] == '合成上传.txt' and document['version'] == 1
        reparsed = client.post('/api/admin/documents/'+document['id']+'/reparse',headers=headers)
        assert reparsed.status_code == 202
        assert service.list_documents()[0]['version'] == 1
        assert worker.run_once()
        documents = service.list_documents()
        assert len(documents) == 1 and documents[0]['id'] == document['id'] and documents[0]['version'] == 2
        assert client.get('/api/admin/knowledge/jobs/'+reparsed.json()['id']).json()['status'] == 'complete'
        terminal = client.post('/api/admin/knowledge/jobs/'+reparsed.json()['id']+'/cancel',headers=headers)
        assert terminal.status_code == 200 and terminal.json()['status'] == 'complete'
        queued = client.post('/api/admin/documents',headers=headers,files={
            'file':('cancelled.txt',b'Synthetic HTTP cancellation.','text/plain')}).json()
        endpoint = '/api/admin/knowledge/jobs/'+queued['id']+'/cancel'
        assert client.post(endpoint).status_code == 403
        cancelled = client.post(endpoint,headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
        assert not worker.run_once()
        assert client.post('/api/admin/knowledge/jobs/not-an-id/cancel',headers=headers).status_code == 400
        customer = client.post('/api/auth/login',headers={'Origin':policy.origin},json={
            'username':'synthetic.customer','password':'synthetic-password-only','tenant_id':tenant})
        assert customer.status_code == 200
        assert client.post(endpoint,headers={'Origin':policy.origin,
            'X-CSRF-Token':customer.json()['csrf_token']}).status_code == 403


def test_stale_and_deleted_reparse_targets_never_overwrite_or_reappear(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    document = service.ingest('synthetic.txt',b'Synthetic pinned reparse content.')
    jobs = KnowledgeJobService(service,allow_local_worker=False,durable=True)
    first,second = jobs.create_reparse(document['id']),jobs.create_reparse(document['id'])
    worker = KnowledgeWorker(bundle,paths)
    assert worker.run_once() and worker.run_once()
    assert sorted(jobs.get(job['id'])['status'] for job in (first,second)) == ['complete','failed']
    assert len(service.list_documents()) == 1 and service.list_documents()[0]['version'] == 2
    deleted = jobs.create_reparse(document['id'])
    service.delete(document['id'])
    assert worker.run_once()
    assert jobs.get(deleted['id'])['status'] == 'failed' and service.list_documents() == []


def test_reparse_and_job_receipts_remain_tenant_scoped(queue_setup,tmp_path):
    from app.knowledge import KnowledgeService,KnowledgeError
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    document = service.ingest('synthetic.txt',b'Synthetic private reparse content.')
    other = bundle.identity.create_tenant('Synthetic queued action other tenant')
    bundle.identity.provision_user('synthetic.action.other','synthetic-password-only',other,role='admin')
    token = bundle.identity.login('synthetic.action.other','synthetic-password-only',other).token
    foreign = KnowledgeService(repository=bundle.knowledge.authenticated(token),**paths)
    with pytest.raises(KnowledgeError,match='文档不存在'):
        KnowledgeJobService(foreign,durable=True).create_reparse(document['id'])
    job = KnowledgeJobService(service,durable=True).create_reparse(document['id'])
    with pytest.raises(KnowledgeError,match='任务不存在'):
        KnowledgeJobService(foreign,durable=True).get(job['id'])
    with pytest.raises(KnowledgeError,match='任务不存在'):
        KnowledgeJobService(foreign,durable=True).cancel(job['id'])
    assert KnowledgeJobService(service,durable=True).get(job['id'])['status'] == 'pending'


def test_old_worker_cannot_claim_new_actions_without_version_contract(queue_setup,tmp_path):
    import uuid
    import sqlalchemy as sa
    from app.persistence.schema import metadata
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = KnowledgeJobService(service,durable=True).create_upload('synthetic.txt',b'Synthetic current worker input.')
    with pytest.raises(sa.exc.DBAPIError,match='requires current Worker'):
        with bundle.engine.begin() as db:
            jobs = metadata.tables['knowledge_jobs']
            db.execute(jobs.update().where(jobs.c.id == uuid.UUID(job['id'])).values(status='running',
                execution_slot_held=True,lease_owner=uuid.uuid4(),lease_fence=1,
                lease_until=sa.func.clock_timestamp()+sa.text("interval '60 seconds'")))
    assert KnowledgeWorker(bundle,paths).run_once()
    assert service.list_documents()[0]['display_name'] == 'synthetic.txt'


def test_cancel_pending_is_idempotent_audited_and_not_claimed(queue_setup,tmp_path):
    import sqlalchemy as sa
    from app.persistence.schema import metadata
    from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    jobs = KnowledgeJobService(service,durable=True)
    job = jobs.create_upload('synthetic.txt',b'Synthetic cancelled input.')
    assert job['cancellation_supported'] is True
    assert jobs.cancel(job['id'])['status'] == 'cancelled'
    assert jobs.cancel(job['id'])['status'] == 'cancelled'
    assert DurableKnowledgeRepository(bundle.engine).claim_next() is None
    assert service.list_documents() == []
    with bundle.engine.connect() as db:
        audit = metadata.tables['audit_logs']
        assert db.execute(sa.select(sa.func.count()).select_from(audit).where(
            audit.c.action == 'knowledge.job.cancel',audit.c.entity_id == job['id'])).scalar_one() == 1


def test_cancel_running_stops_real_parser_before_releasing_capacity(queue_setup,tmp_path,monkeypatch):
    import subprocess,sys,time,uuid
    import sqlalchemy as sa
    import app.isolated_parser as module
    from app.persistence.schema import metadata
    from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    jobs = KnowledgeJobService(service,durable=True)
    job = jobs.create_upload('synthetic.txt',b'Synthetic running cancellation.')
    root = DurableKnowledgeRepository(bundle.engine)
    claimed = root.claim_next()
    original = subprocess.Popen
    processes = []
    def cancel_child(command,**kwargs):
        process = original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
        processes.append(process)
        assert jobs.cancel(job['id'])['status'] == 'cancelled'
        with bundle.engine.connect() as db:
            table = metadata.tables['knowledge_jobs']
            assert db.execute(sa.select(table.c.execution_slot_held).where(
                table.c.id == uuid.UUID(job['id']))).scalar_one() is True
        return process
    monkeypatch.setattr(module.subprocess,'Popen',cancel_child)
    started = time.monotonic()
    try:
        worker = KnowledgeWorker(bundle,paths,document_parser=IsolatedDocumentParser(timeout_seconds=20))
        assert worker._execute(claimed) == 'coordination_unavailable'
        assert time.monotonic()-started < 10
        assert len(processes) == 1 and processes[0].poll() is not None
        assert service.list_documents() == []
        assert jobs.get(job['id'])['status'] == 'cancelled'
        assert root.reap_expired() == 0
        with bundle.engine.connect() as db:
            table = metadata.tables['knowledge_jobs']
            assert db.execute(sa.select(table.c.execution_slot_held).where(
                table.c.id == uuid.UUID(job['id']))).scalar_one() is False
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)


def test_cancelled_abandoned_slot_requires_explicit_operator_confirmation(queue_setup,tmp_path):
    from app.knowledge_admission import KnowledgeAdmissionAdmin
    from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    jobs = KnowledgeJobService(service,durable=True)
    document = service.ingest('published.txt',b'Synthetic published document retained after cancellation.')
    job = jobs.create_reparse(document['id'])
    root = DurableKnowledgeRepository(bundle.engine)
    lease = root.claim_next()['lease']
    jobs.cancel(job['id'])
    admin = KnowledgeAdmissionAdmin(bundle.engine)
    with pytest.raises(ValueError,match='Confirm'):
        admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence)
    assert len(admin.snapshot()['held']) == 1
    assert admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence,
        parser_stopped_confirmed=True)
    assert admin.snapshot()['held'] == []
    assert service.list_documents()[0]['id'] == document['id']
    assert service.list_documents()[0]['version'] == 1


def test_actual_backup_restores_pending_upload_bytes_and_pinned_reparse(backup_setup,tmp_path):
    import uuid
    import sqlalchemy as sa
    from app.backup import create_backup,restore_backup
    from app.knowledge import KnowledgeService
    from app.persistence.bundle import PostgreSQLRepositories
    from app.persistence.schema import metadata
    bundle,common,package,tenant,token,_ = backup_setup
    service = KnowledgeService(repository=bundle.knowledge.authenticated(token),
        knowledge_dir=common['knowledge_root'],asset_dir=common['asset_root'],legacy_upload_dir=tmp_path/'legacy')
    document = service.list_documents()[0]
    jobs = KnowledgeJobService(service,durable=True)
    jobs.create_upload('synthetic-pending.txt',b'Synthetic queued bytes preserved by encrypted backup.')
    jobs.create_reparse(document['id'])
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        knowledge_root,asset_root = tmp_path/'restored-docs',tmp_path/'restored-assets'
        restore_backup(**{**common,'url':url,'knowledge_root':knowledge_root,'asset_root':asset_root},
            source=package,isolated_confirmed=True)
        restored = PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            with restored.engine.connect() as db:
                rows = db.execute(sa.select(metadata.tables['knowledge_job_items'])).mappings().all()
            assert len(rows) == 2
            for row in rows:
                assert (knowledge_root/str(row['tenant_id'])/row['relative_path']).is_file()
            target = next(row for row in rows if row['replace_document_id'] is not None)
            assert target['replace_document_id'] == uuid.UUID(document['id'])
            assert target['replace_document_version'] == document['version']
        finally:
            restored.close()
