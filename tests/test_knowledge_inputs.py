"""Cross-process object inputs and restore on real disposable PG + S3."""
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
from threading import Thread
import uuid
import pytest
import sqlalchemy as sa
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from alembic import command
from alembic.config import Config
from app.knowledge import KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_worker import KnowledgeWorker
from app.object_store import ObjectReference
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.schema import metadata
from app.tracing import SafeTracing, capture_carrier
from app.otlp_export import _INHERITED_CONFIG
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database
from test_object_store import real_s3
from test_object_backup import restore_store
from test_logical_backup import empty_database


@pytest.fixture
def inputs(queue_setup, real_s3, tmp_path):
    bundle, _, _, tokens = queue_setup
    store, client = real_s3
    paths = {'knowledge_dir':tmp_path/'api-docs', 'asset_dir':tmp_path/'api-assets',
        'legacy_upload_dir':tmp_path/'api-legacy', 'object_store':store}
    service = KnowledgeService(repository=bundle.knowledge.authenticated(tokens['synthetic.admin']), **paths)
    service.knowledge_dir.mkdir(parents=True)
    source = service.knowledge_dir/'input'
    source.mkdir()
    payload = b'Synthetic remote queued evidence, immutable across parser hosts.'
    (source/'synthetic.txt').write_bytes(payload)
    return bundle, tokens, paths, service, source, store, client, payload


def enqueue(service, source):
    return KnowledgeJobService(service, allow_local_worker=False, durable=True).create(str(source))


def worker(bundle, store, tmp_path):
    return KnowledgeWorker(bundle, {'knowledge_dir':tmp_path/'other-docs', 'asset_dir':tmp_path/'other-assets',
        'legacy_upload_dir':tmp_path/'other-legacy', 'object_store':store})


def test_real_independent_process_reads_immutable_inputs_without_shared_root(inputs, postgres_database, tmp_path):
    bundle, tokens, paths, service, source, store, client, payload = inputs
    (source/'unsupported.bin').write_bytes(b'Synthetic unsupported')
    job = enqueue(service, source)
    (source/'synthetic.txt').unlink()
    (source/'unsupported.bin').unlink()
    result = subprocess.run([sys.executable, 'tests/fixtures/object_knowledge_worker.py', postgres_database,
        store.bucket, str(tmp_path/'separate')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    state = KnowledgeJobService(service).get(job['id'])
    assert state['status'] == 'complete' and state['imported_count'] == 1 and state['skipped_count'] == 1
    assert 'input_object' not in str(state)
    doc = service.list_documents()[0]
    assert service.search('immutable parser hosts')[0]['document_id'] == doc['id']
    assert not list((tmp_path/'separate').rglob('*.txt'))
    # A fresh host can reparse the published version as well.
    other = KnowledgeService(repository=service.repository, object_store=store,
        knowledge_dir=tmp_path/'fresh-docs', asset_dir=tmp_path/'fresh-assets')
    assert other.reparse(doc['id'])['version'] == 2


def test_real_queued_upload_and_reparse_use_private_inputs_across_worker_roots(inputs,postgres_database,tmp_path):
    bundle,tokens,paths,service,source,store,client,payload = inputs
    jobs = KnowledgeJobService(service,allow_local_worker=False,durable=True)
    uploaded = jobs.create_upload('synthetic-upload.txt',payload)
    result = subprocess.run([sys.executable,'tests/fixtures/object_knowledge_worker.py',
        postgres_database,store.bucket,str(tmp_path/'upload-worker')],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0,result.stderr
    assert jobs.get(uploaded['id'])['status'] == 'complete'
    document = service.list_documents()[0]
    assert document['display_name'] == 'synthetic-upload.txt' and document['version'] == 1
    reparsed = jobs.create_reparse(document['id'])
    result = subprocess.run([sys.executable,'tests/fixtures/object_knowledge_worker.py',
        postgres_database,store.bucket,str(tmp_path/'reparse-worker')],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0,result.stderr
    assert jobs.get(reparsed['id'])['status'] == 'complete'
    documents = service.list_documents()
    assert len(documents) == 1 and documents[0]['id'] == document['id'] and documents[0]['version'] == 2
    assert not list((tmp_path/'upload-worker').rglob('*.txt'))
    assert not list((tmp_path/'reparse-worker').rglob('*.txt'))


def test_real_independent_worker_exports_private_object_child_spans(inputs,postgres_database,tmp_path,monkeypatch):
    for name in _INHERITED_CONFIG:
        monkeypatch.delenv(name,raising=False)
    bundle,tokens,paths,service,source,store,client,payload = inputs
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
    tracing = SafeTracing(InMemorySpanExporter())
    try:
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())) as submitted:
            carrier = capture_carrier()
            job = enqueue(service,source)
        (source/'synthetic.txt').unlink()  # Worker must read the private version, not a shared path.
        result = subprocess.run([sys.executable,'tests/fixtures/object_knowledge_worker.py',postgres_database,
            store.bucket,str(tmp_path/'trace-worker'),'--otlp-traces-endpoint',
            f'http://127.0.0.1:{server.server_port}/v1/traces'],capture_output=True,text=True,timeout=30)
        assert result.returncode == 0,result.stderr
        assert KnowledgeJobService(service).get(job['id'])['status'] == 'complete'
        spans = [span for blob in received for resource in ExportTraceServiceRequest.FromString(blob).resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        def attributes(span):
            return {item.key:item.value.string_value for item in span.attributes if item.value.HasField('string_value')}
        worker = next(span for span in spans if span.name == 'worker.knowledge')
        objects = [span for span in spans if span.name == 'object.request']
        assert int.from_bytes(worker.parent_span_id,'big') == submitted.context.span_id
        assert int.from_bytes(worker.trace_id,'big') == submitted.context.trace_id
        assert attributes(worker)['request_id'] == carrier['request_id']
        assert attributes(worker)['job_id'] == job['id']
        assert {'get','put','check_ready'} <= {attributes(span)['object.operation'] for span in objects}
        assert all(span.trace_id == worker.trace_id and attributes(span)['job_id'] == job['id'] for span in objects)
        assert any(span.parent_span_id == worker.span_id for span in objects)
        assert all(b'Synthetic remote queued evidence' not in blob and b'synthetic-s3-secret-only' not in blob
                   for blob in received)
    finally:
        tracing.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('failure', ['missing_version', 'unconfigured'])
def test_real_object_input_never_falls_back_to_local_file(inputs, tmp_path, failure):
    bundle, tokens, paths, service, source, store, client, payload = inputs
    job = enqueue(service, source)
    with bundle.engine.connect() as db:
        ref = db.execute(sa.select(metadata.tables['knowledge_job_items'].c.input_object)).scalar_one()
    if failure == 'missing_version':
        client.delete_object(Bucket=ref['bucket'], Key=ref['key'], VersionId=ref['version_id'])
        client.put_object(Bucket=ref['bucket'], Key=ref['key'], Body=payload)
    runner = worker(bundle, store if failure == 'missing_version' else None, tmp_path)
    assert runner.run_once()
    state = KnowledgeJobService(service).get(job['id'])
    assert state['status'] == 'failed' and state['failed_count'] == 1
    assert service.list_documents() == [] and (source/'synthetic.txt').read_bytes() == payload


def test_real_input_revocation_after_download_prevents_parsing(inputs, tmp_path, monkeypatch):
    bundle, tokens, paths, service, source, store, client, payload = inputs
    enqueue(service, source)
    original = store.get
    def revoked(*args, **kwargs):
        data = original(*args, **kwargs)
        bundle.identity.logout(tokens['synthetic.admin'])
        return data
    monkeypatch.setattr(store, 'get', revoked)
    import app.knowledge as module
    monkeypatch.setattr(module, 'parse_structured_document', lambda *args: pytest.fail('Revoked input was parsed'))
    assert worker(bundle, store, tmp_path).run_once()
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['documents'])).scalar_one() == 0
        assert db.execute(sa.select(metadata.tables['knowledge_job_items'].c.status)).scalar_one() == 'pending'


def test_real_pending_input_backup_restore_remaps_composite_item_identity(inputs, postgres_database, tmp_path):
    from app.backup import create_backup, restore_backup
    bundle, tokens, paths, service, source, store, client, payload = inputs
    job = enqueue(service, source)
    # Two positions in the same job must not collapse into one mapping key.
    (source/'second.txt').write_bytes(b'Synthetic second input with different content')
    other = enqueue(service, source)
    binaries = os.environ.get('TEST_POSTGRES_BIN')
    if not binaries:
        pytest.skip('TEST_POSTGRES_BIN unset; object input restore unverified')
    paths['asset_dir'].mkdir()
    keys = tmp_path/'keys'
    keys.mkdir()
    key = keys/'backup.key'
    key.write_bytes(os.urandom(32))
    packages = tmp_path/'packages'
    packages.mkdir()
    common = {'url':postgres_database, 'binary_dir':Path(binaries), 'knowledge_root':paths['knowledge_dir'],
        'asset_root':paths['asset_dir'], 'key_path':key, 'scratch_root':tmp_path}
    package = packages/'queued-backup'
    report = create_backup(**common, destination=package, maintenance_confirmed=True, object_store=store)
    assert report['objects'] == 3
    with empty_database() as url, restore_store(client) as target:
        report = restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
            'asset_root':tmp_path/'restored-assets'}, source=package, isolated_confirmed=True, object_store=target)
        assert report['objects_remapped'] == 3 and report['recovery_hold']
        restored = PostgreSQLRepositories.from_url(url, pool_size=1)
        try:
            with restored.engine.connect() as db:
                items = db.execute(sa.select(metadata.tables['knowledge_job_items'])).mappings().all()
                assert len(items) == 3 and {str(row['job_id']) for row in items} == {job['id'],other['id']}
                for row in items:
                    ref = ObjectReference.from_dict(row['input_object'])
                    assert ref.bucket == target.bucket and target.get(row['tenant_id'],ref)
            from app.recovery import RecoveryHold
            with pytest.raises(RecoveryHold):
                DurableKnowledgeRepository(restored.engine).claim_next()
        finally:
            restored.close()


def test_real_upgrade_0014_keeps_old_local_item_input_null(postgres_database):
    config = Config('alembic.ini')
    command.upgrade(config, '0014_recovery_hold')
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=1)
    try:
        tenant = bundle.identity.create_tenant('Synthetic pre-object input tenant')
        user = bundle.identity.provision_user('synthetic.old-input', 'synthetic-password-only', tenant, role='admin')
        # provision_user returns the user ID, not an authenticated command.
        from app.database import utc_now
        job = uuid.uuid4()
        with bundle.engine.begin() as db:
            db.execute(metadata.tables['knowledge_jobs'].insert().values(id=job, tenant_id=uuid.UUID(tenant),
                requested_by=uuid.UUID(user), source_path='synthetic-old-source', recursive=True,
                status='pending', stage='pending', created_at=utc_now()))
            db.execute(metadata.tables['knowledge_job_items'].insert().values(job_id=job,position=0,
                tenant_id=uuid.UUID(tenant),relative_path='synthetic.txt',size=4,sha256='a'*64))
        command.upgrade(config, 'head')
        with bundle.engine.connect() as db:
            row = db.execute(sa.select(metadata.tables['knowledge_job_items'])).mappings().one()
            assert row['input_object'] is None and row['relative_path'] == 'synthetic.txt' and row['size'] == 4
    finally:
        bundle.close()


def test_real_restore_hold_rechecked_inside_input_enqueue_transaction(inputs):
    from app.recovery import RecoveryHold
    bundle, tokens, paths, service, source, store, client, payload = inputs
    enqueue(service, source)
    table = metadata.tables['knowledge_job_items']
    with bundle.engine.begin() as db:
        item = dict(db.execute(sa.select(table.c.position,table.c.relative_path,table.c.size,
            table.c.sha256,table.c.input_object)).mappings().one())
        db.execute(metadata.tables['recovery_state'].insert().values(id=1,blocked=True,
            backup_id=uuid.uuid4(),restored_at=sa.func.clock_timestamp()))
    queue = DurableKnowledgeRepository(bundle.engine, token=tokens['synthetic.admin'])
    with pytest.raises(RecoveryHold):
        queue.enqueue(str(uuid.uuid4()),'synthetic-source',True,[item])
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['knowledge_jobs'])).scalar_one() == 1
