"""Real PG/isolated files, real parsers and independent processes; no model keys."""
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
import pytest
import sqlalchemy as sa
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from app.auth_http import AuthPolicy
from app.knowledge import KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_worker import KnowledgeWorker
from app.main import create_app
from app.otlp_export import PrivateOtlpTracing, _INHERITED_CONFIG
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository, KnowledgeLease, KnowledgeLeaseLost
from app.persistence.schema import metadata
from app.security import SecretStore
from app.tracing import SafeTracing, capture_carrier
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database


def setup(queue_setup,tmp_path):
    bundle,_,_,tokens = queue_setup
    paths = {'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'}
    service = KnowledgeService(repository=bundle.knowledge.authenticated(tokens['synthetic.admin']),**paths)
    service.knowledge_dir.mkdir(parents=True)
    source = service.knowledge_dir/'input'
    source.mkdir()
    (source/'synthetic.txt').write_text('Synthetic durable knowledge evidence with immutable publication.',encoding='utf-8')
    return bundle,tokens,paths,service,source


def enqueue(service,source):
    return KnowledgeJobService(service,allow_local_worker=False,durable=True).create(str(source))


def worker_command(url,paths):
    return [sys.executable,'scripts/knowledge_worker.py','--database-url',url,
        '--knowledge-root',str(paths['knowledge_dir']),'--asset-root',str(paths['asset_dir']),
        '--legacy-root',str(paths['legacy_upload_dir']),'--once']


def test_knowledge_queue_internal_trace_carrier_and_worker_parent(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    queue = metadata.tables['knowledge_job_queue']
    try:
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())) as submitted:
            carrier = capture_carrier()
            job = enqueue(service,source)
        jid = uuid.UUID(job['id'])
        with bundle.engine.connect() as db:
            stored = db.execute(sa.select(queue.c.trace_context).where(queue.c.job_id == jid)).scalar_one()
        assert stored == carrier
        assert 'trace_context' not in job
        for invalid in ({**carrier,'document':'SECRET_BODY'}, {**carrier,'sampled':'yes'},
                        {**carrier,'trace_id':'0'*32}):
            with pytest.raises(sa.exc.IntegrityError):
                with bundle.engine.begin() as db:
                    db.execute(queue.update().where(queue.c.job_id == jid).values(trace_context=invalid))
        assert KnowledgeWorker(bundle,paths,tracing=tracing).run_once()
        assert KnowledgeJobService(service).get(job['id'])['status'] == 'complete'
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        worker = next(span for span in spans if span.name == 'worker.knowledge')
        assert worker.parent.span_id == submitted.context.span_id
        assert worker.context.trace_id == submitted.context.trace_id
        assert worker.attributes['request_id'] == carrier['request_id']
        assert worker.attributes['job_id'] == job['id']
        assert any(span.name == 'db.query' and span.parent is not None and
                   span.context.trace_id == worker.context.trace_id for span in spans)
        assert all('SECRET_' not in str(dict(span.attributes)) for span in spans)
    finally:
        tracing.close()


def test_authenticated_api_to_independent_knowledge_worker_otlp(queue_setup,postgres_database,tmp_path,monkeypatch):
    for name in _INHERITED_CONFIG:
        monkeypatch.delenv(name,raising=False)
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    received = []
    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/v1/traces':
                self.send_error(404)
                return
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            self.send_header('Content-Length','0')
            self.end_headers()
        def log_message(self,*_):
            pass
    server = HTTPServer(('127.0.0.1',0),Collector)
    thread = Thread(target=server.serve_forever,daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}/v1/traces'
    tracing = PrivateOtlpTracing(endpoint)
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths=paths,auth_policy=policy,durable_knowledge=True,tracing=tracing)
    try:
        tenant = bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
        with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
            login = client.post('/api/auth/login',json={'tenant_id':tenant,'username':'synthetic.admin',
                'password':'synthetic-password-only'},headers={'Origin':policy.origin})
            assert login.status_code == 200
            headers = {'Origin':policy.origin,'X-CSRF-Token':login.json()['csrf_token']}
            result = client.post('/api/admin/knowledge/jobs',headers={**headers,
                'traceparent':'00-'+'a'*32+'-'+'b'*16+'-01','baggage':'SECRET_BAGGAGE'},
                json={'path':str(source),'recursive':True})
            assert result.status_code == 202, result.text
            assert 'trace_context' not in result.text
            job_id = result.json()['id']
            process = subprocess.run(worker_command(postgres_database,paths)+['--otlp-traces-endpoint',endpoint],
                capture_output=True,text=True,timeout=30)
            assert process.returncode == 0, process.stderr
            assert client.get(f'/api/admin/knowledge/jobs/{job_id}',headers=headers).json()['status'] == 'complete'
            assert tracing.flush(3)
        spans = [span for blob in received for resource in ExportTraceServiceRequest.FromString(blob).resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        def attributes(span):
            return {item.key:item.value.string_value if item.value.HasField('string_value') else item.value.bool_value
                    for item in span.attributes}
        api = next(span for span in spans if span.name == 'http.request' and
                   attributes(span).get('request_id') == result.headers['X-Request-ID'])
        worker = next(span for span in spans if span.name == 'worker.knowledge' and
                      attributes(span).get('job_id') == job_id)
        assert worker.trace_id == api.trace_id and worker.parent_span_id == api.span_id
        assert attributes(worker)['request_id'] == result.headers['X-Request-ID']
        assert any(span.name == 'db.query' and span.trace_id == worker.trace_id and
                   attributes(span).get('job_id') == job_id for span in spans)
        assert all(b'SECRET_' not in blob for blob in received)
    finally:
        tracing.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def expire(bundle,jid):
    j = metadata.tables['knowledge_jobs']
    with bundle.engine.begin() as db:
        db.execute(j.update().where(j.c.id == uuid.UUID(jid)).values(lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))


def test_separate_worker_fixed_manifest_partial_results_and_immutable_files(queue_setup,postgres_database,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    (source/'unsupported.bin').write_bytes(b'Not parsed')
    (source/'empty.txt').write_bytes(b'')
    changed = source/'changed.txt'
    changed.write_text('Original synthetic snapshot',encoding='utf-8')
    job = enqueue(service,source)
    changed.write_text('Mutated synthetic source',encoding='utf-8')
    result = subprocess.run(worker_command(postgres_database,paths),capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    state = KnowledgeJobService(service).get(job['id'])
    assert state['status'] == 'partial'
    assert (state['imported_count'],state['skipped_count'],state['failed_count'],state['processed_count']) == (1,1,2,4)
    assert not {'lease_owner','lease_fence','lease_until','execution_mode','session_hash'} & state.keys()
    docs = service.list_documents()
    assert len(docs) == 1 and docs[0]['version'] == 1
    stored = service.knowledge_dir/docs[0]['stored_name']
    assert stored != source/'synthetic.txt' and stored.read_bytes() == (source/'synthetic.txt').read_bytes()
    assert source.joinpath('synthetic.txt').exists()
    source.joinpath('synthetic.txt').write_text('Changed after publication',encoding='utf-8')
    assert 'immutable publication' in stored.read_text(encoding='utf-8')
    again = subprocess.run(worker_command(postgres_database,paths),capture_output=True,text=True,timeout=30)
    assert again.returncode == 0 and len(service.list_documents()) == 1


def test_partial_import_trace_reflects_committed_job_status(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    (source/'invalid.txt').write_bytes(b'')
    job = enqueue(service,source)
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        assert KnowledgeWorker(bundle,paths,tracing=tracing).run_once()
        assert KnowledgeJobService(service).get(job['id'])['status'] == 'partial'
        assert tracing.flush()
        span = next(span for span in exporter.get_finished_spans() if span.name == 'worker.knowledge')
        assert span.attributes['outcome'] == 'error'
        assert span.status.status_code.name == 'ERROR'
    finally:
        tracing.close()


@pytest.mark.parametrize('failure',['owner','fence','expiry','credential'])
def test_one_claim_and_stale_parser_cannot_publish_or_remove_source(queue_setup,tmp_path,failure,monkeypatch):
    import io
    from PIL import Image
    import app.knowledge as module
    from app.knowledge_models import EmbeddedAsset
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    root = DurableKnowledgeRepository(bundle.engine)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _:root.claim_next(),range(4)))
    assert sum(item is not None for item in claims) == 1
    selected = next(item for item in claims if item)
    lease = selected['lease']
    if failure == 'owner':
        lease = KnowledgeLease(lease.job_id,str(uuid.uuid4()),lease.fence)
    elif failure == 'fence':
        lease = KnowledgeLease(lease.job_id,lease.owner,lease.fence+1)
    elif failure == 'expiry':
        expire(bundle,job['id'])
    credential = selected['credential']
    if failure == 'credential':
        credential = bundle.identity.login('synthetic.admin','synthetic-password-only',bundle.identity.resolve(tokens['synthetic.admin']).tenant_id).token
    old = root.authenticated(credential).with_lease(lease).with_item(0)
    old_service = KnowledgeService(repository=old,**paths)
    original = module.parse_structured_document
    buf = io.BytesIO()
    Image.new('RGB',(1,1),'white').save(buf,format='PNG')
    png = buf.getvalue()
    def with_asset(*args):
        parsed = original(*args)
        parsed.assets = [EmbeddedAsset(0,png,'image/png','.png','Synthetic','Synthetic',1,1)]
        return parsed
    monkeypatch.setattr(module,'parse_structured_document',with_asset)
    untouched = old_service.asset_dir/'unrelated'/'keep.png'
    untouched.parent.mkdir(parents=True)
    untouched.write_bytes(png)
    with pytest.raises(KnowledgeLeaseLost):
        old_service.ingest('synthetic.txt',(source/'synthetic.txt').read_bytes(),source_path=source/'synthetic.txt',immutable_copy=True)
    assert service.list_documents() == []
    assert list(service.knowledge_dir.glob('*.txt')) == []
    assert source.joinpath('synthetic.txt').exists()
    assert list(old_service.asset_dir.glob('**/*.png')) == [untouched]
    assert untouched.read_bytes() == png
    assert bundle.knowledge.authenticated(tokens['synthetic.admin']).update_job(job['id'],{'status':'complete'}) is False
    if failure == 'expiry':
        assert root.reap_expired() == 1
        state = KnowledgeJobService(service).get(job['id'])
        assert state['status'] == 'failed' and state['stage'] == 'lease_expired'
        assert root.reap_expired() == 0 and root.claim_next() is None


def test_lost_lease_terminates_isolated_parser_without_publication(queue_setup,tmp_path,monkeypatch):
    import time
    import app.isolated_parser as module
    from app.isolated_parser import IsolatedDocumentParser

    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    root = DurableKnowledgeRepository(bundle.engine)
    claimed = root.claim_next()
    original = subprocess.Popen
    processes = []
    def slow_child(command, **kwargs):
        process = original([sys.executable,'-c','import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        expire(bundle,job['id'])
        return process
    monkeypatch.setattr(module.subprocess,'Popen',slow_child)
    worker = KnowledgeWorker(bundle,paths,document_parser=IsolatedDocumentParser(timeout_seconds=20))
    started = time.monotonic()
    try:
        assert worker._execute(claimed) == 'coordination_unavailable'
        assert time.monotonic()-started < 10
        assert len(processes) == 1 and processes[0].poll() is not None
        with bundle.engine.connect() as db:
            jobs = metadata.tables['knowledge_jobs']
            assert db.execute(sa.select(jobs.c.execution_slot_held).where(
                jobs.c.id == uuid.UUID(job['id']))).scalar_one() is False
        assert service.list_documents() == []
        assert (source/'synthetic.txt').exists()
        assert root.reap_expired() == 1
        assert KnowledgeJobService(service).get(job['id'])['stage'] == 'lease_expired'
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_unclaimed_knowledge_deadline_is_distinct_from_unknown_execution(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    q = metadata.tables['knowledge_job_queue']
    with bundle.engine.begin() as db:
        db.execute(q.update().where(q.c.job_id == uuid.UUID(job['id'])).values(
            deadline=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    root = DurableKnowledgeRepository(bundle.engine)
    assert root.reap_expired() == 1
    state = KnowledgeJobService(service).get(job['id'])
    assert state['status'] == 'failed' and state['stage'] == 'queue_timeout'
    assert state['processed_count'] == 0
    assert root.claim_next() is None and root.reap_expired() == 0
    assert service.list_documents() == []


def test_publication_progress_failure_rolls_back_document_and_owned_files(queue_setup,tmp_path,monkeypatch):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    root = DurableKnowledgeRepository(bundle.engine)
    selected = root.claim_next()
    old = root.authenticated(selected['credential']).with_lease(selected['lease']).with_item(0)
    def broken(*args):
        raise RuntimeError('Injected progress commit failure')
    monkeypatch.setattr(DurableKnowledgeRepository,'_sync',broken)
    with pytest.raises(RuntimeError):
        KnowledgeService(repository=old,**paths).ingest('synthetic.txt',(source/'synthetic.txt').read_bytes(),
            source_path=source/'synthetic.txt',immutable_copy=True)
    assert service.list_documents() == [] and source.joinpath('synthetic.txt').exists()
    assert list(service.knowledge_dir.glob('*.txt')) == []
    assert old.manifest()[0]['status'] == 'pending'
    assert KnowledgeJobService(service).get(job['id'])['processed_count'] == 0


def test_revocation_during_parse_prevents_publish_then_expiry_closes_without_replay(queue_setup,tmp_path,monkeypatch):
    import app.knowledge as module
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    original = module.parse_structured_document
    def revoked(*args):
        parsed = original(*args)
        bundle.identity.logout(tokens['synthetic.admin'])
        return parsed
    monkeypatch.setattr(module,'parse_structured_document',revoked)
    assert KnowledgeWorker(bundle,paths).run_once()
    token = bundle.identity.login('synthetic.admin','synthetic-password-only',bundle.identity.resolve(tokens['synthetic.customer']).tenant_id).token
    recovered = KnowledgeService(repository=bundle.knowledge.authenticated(token),**paths)
    assert recovered.list_documents() == []
    expire(bundle,job['id'])
    root = DurableKnowledgeRepository(bundle.engine)
    assert root.reap_expired() == 1
    assert KnowledgeJobService(recovered).get(job['id'])['status'] == 'failed'
    assert root.claim_next() is None and source.joinpath('synthetic.txt').exists()


def test_killed_parser_process_expires_without_reclaiming_or_publishing(queue_setup,postgres_database,tmp_path):
    import queue
    import threading
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    code = """import sys,time
from pathlib import Path
import app.knowledge as knowledge
from app.persistence.bundle import PostgreSQLRepositories
from app.knowledge_worker import KnowledgeWorker
original = knowledge.parse_structured_document
def slow(*args):
    print('synthetic-parser-entered',flush=True)
    time.sleep(60)
    return original(*args)
knowledge.parse_structured_document = slow
bundle = PostgreSQLRepositories.from_url(sys.argv[1],pool_size=3)
try:
    KnowledgeWorker(bundle,{'knowledge_dir':Path(sys.argv[2]),'asset_dir':Path(sys.argv[3]),'legacy_upload_dir':Path(sys.argv[4])}).run_once()
finally:
    bundle.close()
"""
    process = subprocess.Popen([sys.executable,'-c',code,postgres_database,*[str(paths[key]) for key in ('knowledge_dir','asset_dir','legacy_upload_dir')]],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    observed = queue.Queue()
    thread = threading.Thread(target=lambda:observed.put(process.stdout.readline()),daemon=True)
    thread.start()
    try:
        assert observed.get(timeout=15).strip() == 'synthetic-parser-entered'
        assert KnowledgeJobService(service).get(job['id'])['status'] == 'running'
    finally:
        process.terminate()
        process.wait(timeout=10)
        thread.join(timeout=2)
    expire(bundle,job['id'])
    root = DurableKnowledgeRepository(bundle.engine)
    assert root.reap_expired() == 1 and root.claim_next() is None
    assert KnowledgeJobService(service).get(job['id'])['processed_count'] == 0
    assert service.list_documents() == [] and source.joinpath('synthetic.txt').exists()


def test_http_only_enqueues_private_manifest_and_independent_worker_publishes(queue_setup,postgres_database,tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.auth_http import AuthPolicy
    from app.security import SecretStore
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    tenant = bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    other = bundle.identity.create_tenant('Synthetic external knowledge tenant')
    bundle.identity.provision_user('synthetic.external','synthetic-password-only',other,role='admin')
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle,auth_policy=policy,durable_knowledge=True,
        secret_store=SecretStore(path=tmp_path/'unused.key'),knowledge_paths=paths)
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        def login(name,tid):
            client.cookies.clear()
            response = client.post('/api/auth/login',headers={'Origin':policy.origin},json={
                'username':name,'password':'synthetic-password-only','tenant_id':tid})
            assert response.status_code == 200
            return {'Origin':policy.origin,'X-CSRF-Token':response.json()['csrf_token']}
        headers = login('synthetic.admin',tenant)
        response = client.post('/api/admin/knowledge/jobs',headers=headers,json={'path':str(source),'recursive':True})
        assert response.status_code == 202 and response.json()['status'] == 'pending'
        jid = response.json()['id']
        assert not any(field in response.text for field in ('session_hash','lease_owner','lease_fence','relative_path'))
        assert service.list_documents() == [] and app.state.knowledge_jobs._executor is None
        assert client.post('/api/admin/knowledge/jobs',headers=headers,json={'path':str(tmp_path)}).status_code == 400
        login('synthetic.external',other)
        assert client.get('/api/admin/knowledge/jobs').json() == []
        assert client.get('/api/admin/knowledge/jobs/'+jid).status_code == 404
        login('synthetic.customer',tenant)
        assert client.get('/api/admin/knowledge/jobs/'+jid).status_code == 403
        login('synthetic.admin',tenant)
        executed = subprocess.run(worker_command(postgres_database,paths),capture_output=True,text=True,timeout=30)
        assert executed.returncode == 0,executed.stderr
        result = client.get('/api/admin/knowledge/jobs/'+jid)
        assert result.status_code == 200 and result.json()['status'] == 'complete'
        assert result.json()['processed_count'] == result.json()['imported_count'] == 1
        assert len(client.get('/api/admin/documents').json()) == 1
    assert not (tmp_path/'unused.key').exists()


def test_upgrading_preexisting_mutable_source_never_deletes_input(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    input_file = source/'synthetic.txt'
    prior = service.ingest(input_file.name,input_file.read_bytes(),source_path=input_file)
    assert service.knowledge_dir/service.list_documents()[0]['stored_name'] == input_file
    input_file.write_text('A changed synthetic document migrating to an immutable owned copy.',encoding='utf-8')
    job = enqueue(service,input_file)
    assert KnowledgeWorker(bundle,paths).run_once()
    state = KnowledgeJobService(service).get(job['id'])
    assert state['status'] == 'complete' and state['imported_count'] == 1
    doc = service.list_documents()[0]
    assert doc['id'] == prior['id'] and doc['version'] == 2
    assert input_file.exists() and service.knowledge_dir/doc['stored_name'] != input_file


def test_lost_commit_ack_preserves_published_files_and_atomic_item(queue_setup,tmp_path,monkeypatch):
    from sqlalchemy.exc import OperationalError
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    job = enqueue(service,source)
    root = DurableKnowledgeRepository(bundle.engine)
    selected = root.claim_next()
    owned = root.authenticated(selected['credential']).with_lease(selected['lease']).with_item(0)
    original = owned.publish_document
    def ack_lost(*args,**kwargs):
        original(*args,**kwargs)
        raise OperationalError('synthetic-commit-ack-lost',{},RuntimeError('Synthetic transport failure'))
    monkeypatch.setattr(owned,'publish_document',ack_lost)
    with pytest.raises(OperationalError):
        KnowledgeService(repository=owned,**paths).ingest('synthetic.txt',(source/'synthetic.txt').read_bytes(),
            source_path=source/'synthetic.txt',immutable_copy=True)
    doc = service.list_documents()[0]
    assert service.knowledge_dir.joinpath(doc['stored_name']).read_bytes() == source.joinpath('synthetic.txt').read_bytes()
    assert owned.manifest()[0]['status'] == 'imported'
    assert KnowledgeJobService(service).get(job['id'])['processed_count'] == 1
    with pytest.raises(KnowledgeLeaseLost):
        owned.record('failed',{'reason':'May not downgrade committed publication'})
