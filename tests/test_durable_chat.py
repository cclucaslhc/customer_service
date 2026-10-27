"""Durable commands on disposable PostgreSQL; no production data/model clients."""
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.identity import AuthenticationError, token_hash
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.durable_chat_repository import DurableChatRepository, DurableChatConflict
from app.run_leases import RunLease, RunLeaseLost
from test_postgresql_integration import postgres_database
from test_production_migration import snapshot


@pytest.fixture
def queue_setup(postgres_database):
    command.upgrade(Config('alembic.ini'), 'head')
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=4)
    tenant = bundle.identity.create_tenant('Synthetic durable queue')
    tokens = {}
    for name, role in (('synthetic.admin', 'admin'), ('synthetic.customer', 'customer'), ('synthetic.other', 'customer')):
        bundle.identity.provision_user(name, 'synthetic-password-only', tenant, role=role)
        tokens[name] = bundle.identity.login(name, 'synthetic-password-only', tenant).token
    bundle.config.authenticated(tokens['synthetic.admin']).initialize_tenant(datetime.now(timezone.utc).isoformat())
    root = DurableChatRepository(bundle.engine)
    owner = root.authenticated(tokens['synthetic.customer'])
    try:
        yield bundle, root, owner, tokens
    finally:
        bundle.close()


def submit(owner, *, sid=None, rid=None, content='Synthetic question'):
    sid, rid = sid or str(uuid.uuid4()), rid or str(uuid.uuid4())
    owner.enqueue(session_id=sid, request_id=rid, content=content, title='Synthetic',
        provider='deepseek', model='synthetic', prompt_version_id=None, instructions='Answer the synthetic question.')
    return sid, rid


def test_durable_claim_once_idempotency_fencing_and_atomic_completion(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    assert submit(owner, sid=sid, rid=rid) == (sid, rid)
    assert len(owner.history(sid)) == 1
    with pytest.raises(DurableChatConflict):
        submit(owner, sid=sid, rid=rid, content='Changed')
    other = root.authenticated(tokens['synthetic.other'])
    assert other.read_events(sid, rid) is None
    with pytest.raises(DurableChatConflict):
        submit(other, sid=sid, rid=rid)
    with pytest.raises(AuthenticationError):
        bundle.identity.resolve(token_hash(tokens['synthetic.customer']))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: root.claim_next(), range(2)))
    assert sum(job is not None for job in jobs) == 1
    job = next(job for job in jobs if job)
    assert bundle.identity.resolve(job['credential']).user_id == bundle.identity.resolve(tokens['synthetic.customer']).user_id
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    with pytest.raises(RunLeaseLost):
        owner.with_lease(RunLease(rid, str(uuid.uuid4()), job['lease'].fence)).append_event(rid, 'token', {'text': 'stale'})
    assert owned.append_event(rid, 'token', {'text': 'Synthetic answer'}) == 3
    message_id = str(uuid.uuid4())
    assert owned.complete_run(run_id=rid, session_id=sid, message_id=message_id, content='Synthetic answer',
        provider='deepseek', model='synthetic', citations=[{'synthetic': True}], now=datetime.now(timezone.utc).isoformat())
    result = owner.read_events(sid, rid)
    assert result['status'] == 'complete'
    assert [row['sequence'] for row in result['events']] == [1, 2, 3, 4, 5, 6, 7]
    assert [row['event'] for row in result['events']] == ['run_started', 'run_queued', 'token', 'answer_reset', 'token', 'citation', 'done']
    assert result['events'][-1]['data']['message_id'] == owner.history(sid)[-1]['id'] == message_id
    assert [row['event'] for row in owner.read_events(sid, rid, after=3)['events']] == ['answer_reset','token','citation', 'done']
    with pytest.raises(DurableChatConflict):
        owner.read_events(sid, rid, after=8)
    with pytest.raises(RunLeaseLost):
        owned.append_event(rid, 'token', {'text': 'late'})
    assert root.claim_next() is None


def test_pre_dispatch_expiry_requeues_once_but_never_replays_after_model_boundary(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = submit(owner)
    first = root.claim_next()
    stale = root.authenticated(first['credential']).with_lease(first['lease'])
    stale.append_event(rid,'provider',{'provider':'deepseek','model':'synthetic'})
    runs = root.tables['agent_runs']
    with bundle.engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert root.reap_expired_leases() == 1
    queued = owner.read_events(sid,rid)
    assert queued['status'] == 'queued'
    assert queued['events'][-1]['event'] == 'run_queued'
    assert queued['events'][-1]['data']['reason'] == 'pre_dispatch_lease_expired'
    with pytest.raises(RunLeaseLost):
        stale.model_meter(rid,provider='deepseek',model='synthetic').begin(str(uuid.uuid4()))
    second = root.claim_next()
    assert second['run_id'] == rid and second['lease'].fence > first['lease'].fence
    assert second['lease'].owner != first['lease'].owner
    with bundle.engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert root.reap_expired_leases() == 1
    assert root.claim_next() is None
    result = owner.read_events(sid,rid)
    assert result['status'] == 'interrupted'
    assert result['events'][-1]['data']['reason'] == 'lease_expired'
    assert [item['role'] for item in owner.history(sid)] == ['user']


def test_pre_dispatch_expiry_with_cancel_is_terminal_not_requeued(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = submit(owner)
    first = root.claim_next()
    assert first['run_id'] == rid
    assert owner.request_cancel(sid,rid)
    runs = root.tables['agent_runs']
    with bundle.engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert root.reap_expired_leases() == 1
    assert root.claim_next() is None
    assert owner.read_events(sid,rid)['status'] == 'interrupted'


def test_durable_cancel_timeout_supersede_and_revoked_identity(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid, old = submit(owner)
    _, new = submit(owner, sid=sid)
    assert owner.read_events(sid, old)['events'][-1]['data']['reason'] == 'superseded'
    assert owner.request_cancel(sid, new)
    assert root.reap_pending() == 1
    assert owner.read_events(sid, new)['events'][-1]['data']['reason'] == 'cancelled'
    sid, expired = submit(owner)
    q = root.tables['chat_run_queue']
    with bundle.engine.begin() as db:
        db.execute(q.update().where(q.c.run_id == uuid.UUID(expired)).values(
            created_at=sa.func.clock_timestamp()-sa.text("interval '3 seconds'"),
            deadline=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert root.reap_pending() == 1
    assert owner.read_events(sid, expired)['events'][-1]['data']['reason'] == 'queue_timeout'
    sid, revoked = submit(owner)
    bundle.identity.logout(tokens['synthetic.customer'])
    assert root.claim_next() is None
    replacement = bundle.identity.login('synthetic.customer', 'synthetic-password-only', bundle.identity.resolve(tokens['synthetic.admin']).tenant_id).token
    events = root.authenticated(replacement).read_events(sid, revoked)['events']
    assert events[-1]['event'] == 'error'
    assert events[-1]['data']['code'] == 'run_identity_revoked'


def worker_command(url, tmp_path, *, delay=0):
    return [sys.executable, 'scripts/chat_worker.py', '--database-url', url,
        '--master-key-path', str(tmp_path/'unused.key'), '--knowledge-root', str(tmp_path/'knowledge'),
        '--asset-root', str(tmp_path/'assets'), '--legacy-root', str(tmp_path/'legacy'),
        '--synthetic', '--synthetic-delay', str(delay), '--once']


def test_separate_worker_process_persists_answer_and_does_not_reexecute(queue_setup, postgres_database, tmp_path):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    result = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    events = owner.read_events(sid, rid)
    assert events['status'] == 'complete'
    assert events['events'][-1]['event'] == 'done'
    assert len(owner.history(sid)) == 2
    assert '独立 Worker' in owner.history(sid)[-1]['content']
    count = events['sequence']
    result = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert owner.read_events(sid, rid)['sequence'] == count
    assert len(owner.history(sid)) == 2
    assert not (tmp_path/'unused.key').exists()


def test_two_independent_worker_processes_claim_one_run_only(queue_setup, postgres_database, tmp_path):
    bundle, root, owner, _tokens = queue_setup
    sid, rid = submit(owner)
    workers = [subprocess.Popen(worker_command(postgres_database, tmp_path, delay=0.3),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for worker in workers:
            _stdout, stderr = worker.communicate(timeout=45)
            assert worker.returncode == 0, stderr.decode(errors='replace')
        events = owner.read_events(sid, rid)
        assert events['status'] == 'complete'
        assert [item['event'] for item in events['events']].count('done') == 1
        assert len([item for item in owner.history(sid) if item['role'] == 'assistant']) == 1
        attempts = root.tables['model_usage_attempts']
        with bundle.engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(attempts).where(
                attempts.c.run_id == uuid.UUID(rid))).scalar_one() == 1
        assert root.claim_next() is None
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.communicate(timeout=10)


def test_killed_worker_is_terminalized_without_replaying_model(queue_setup, postgres_database, tmp_path):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    process = subprocess.Popen(worker_command(postgres_database, tmp_path, delay=60), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+20
        # Provider SSE announces the selected configuration before the paid
        # model boundary. Wait for the durable attempt, not that earlier event,
        # before asserting a killed Worker must never requeue this Run.
        # Read-only polling does not take the session advisory lock.
        runs = root.tables['agent_runs']
        attempts = root.tables['model_usage_attempts']
        def observed_attempt():
            with bundle.engine.connect() as db:
                return db.execute(sa.select(sa.func.count()).select_from(attempts).where(
                    attempts.c.run_id == uuid.UUID(rid))).scalar_one()
        while time.monotonic() < deadline:
            if observed_attempt() > 0:
                break
            assert process.poll() is None
            time.sleep(.05)
        assert observed_attempt() > 0
        assert owner.read_events(sid, rid)['status'] == 'running'
        process.kill()
        process.communicate(timeout=10)
        with bundle.engine.begin() as db:
            db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
                lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
        assert root.reap_expired_leases() == 1
        assert root.claim_next() is None
        result = owner.read_events(sid, rid)
        assert result['status'] == 'interrupted'
        assert result['events'][-1]['event'] == 'interrupted'
        assert [m['role'] for m in owner.history(sid)] == ['user']
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_http_submission_separate_worker_and_cursor_replay(queue_setup, postgres_database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.auth_http import AuthPolicy
    from app.main import create_app
    from app.providers import ModelGateway
    from app.security import SecretStore
    bundle, root, owner, tokens = queue_setup
    def forbidden(*args, **kwargs):
        raise AssertionError('API must not create model clients or execute a queued command')
    monkeypatch.setattr(ModelGateway, 'snapshot', forbidden)
    monkeypatch.setattr(ModelGateway, 'build_model', forbidden)
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle, auth_policy=policy, durable_chat=True,
        secret_store=SecretStore(path=tmp_path/'unused.key'), knowledge_paths={
            'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'})
    tenant = bundle.identity.resolve(tokens['synthetic.customer']).tenant_id
    with TestClient(app, base_url=policy.origin, client=('127.0.0.1', 50000)) as client:
        def login(name):
            client.cookies.clear()
            response = client.post('/api/auth/login', headers={'Origin':policy.origin}, json={
                'username':name,'password':'synthetic-password-only','tenant_id':tenant})
            assert response.status_code == 200
            return {'Origin':policy.origin,'X-CSRF-Token':response.json()['csrf_token']}
        headers = login('synthetic.customer')
        sid = client.post('/api/chat/sessions', headers=headers).json()['id']
        rid = str(uuid.uuid4())
        route = f'/api/chat/sessions/{sid}/messages'
        response = client.post(route, headers=headers, json={'content':'Synthetic HTTP question','request_id':rid})
        assert response.status_code == 202
        assert client.post(route, headers=headers, json={'content':'Synthetic HTTP question','request_id':rid}).status_code == 202
        assert len(client.get(f'/api/chat/sessions/{sid}').json()['messages']) == 1
        result = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        event_url = response.json()['events_url']
        response = client.get(event_url)
        assert response.status_code == 200
        assert 'event: done' in response.text and 'id: 1\n' in response.text
        snapshot = client.get(f'/api/chat/sessions/{sid}/runs/{rid}')
        assert snapshot.status_code == 200 and snapshot.json()['run']['status'] == 'complete'
        assert snapshot.json()['run']['message_id'] == snapshot.json()['messages'][-1]['id']
        assert 'session_hash' not in snapshot.text and 'instructions' not in snapshot.text
        assert 'event: run_started' not in client.get(event_url, headers={'Last-Event-ID':'2'}).text
        assert client.get(event_url, headers={'Last-Event-ID':'-1'}).status_code == 400
        assert client.get(event_url+'?after=9999').status_code == 409
        login('synthetic.other')
        assert client.get(event_url).status_code == 404
        assert client.get(f'/api/chat/sessions/{sid}/runs/{rid}').status_code == 404
        assert client.get(f'/api/chat/sessions/{sid}').status_code == 404
    assert not (tmp_path/'unused.key').exists()


def test_real_http_subscriber_disconnect_does_not_cancel_command(queue_setup, postgres_database, tmp_path):
    import socket
    import threading
    import httpx
    import uvicorn
    from app.auth_http import AuthPolicy
    from app.main import create_app
    from app.security import SecretStore
    bundle, root, owner, tokens = queue_setup
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    app = create_app(repositories=bundle, durable_chat=True,
        auth_policy=AuthPolicy(origin, secure_cookie=False), secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'})
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and time.monotonic() < deadline:
            assert thread.is_alive()
            time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=origin, timeout=10) as client:
            tenant = bundle.identity.resolve(tokens['synthetic.customer']).tenant_id
            response = client.post('/api/auth/login', headers={'Origin':origin}, json={
                'username':'synthetic.customer','password':'synthetic-password-only','tenant_id':tenant})
            assert response.status_code == 200
            headers = {'Origin':origin,'X-CSRF-Token':response.json()['csrf_token']}
            sid = client.post('/api/chat/sessions', headers=headers).json()['id']
            response = client.post(f'/api/chat/sessions/{sid}/messages', headers=headers, json={'content':'Synthetic disconnect'})
            assert response.status_code == 202
            rid = response.json()['run_id']
            with client.stream('GET', response.json()['events_url']) as subscriber:
                assert subscriber.status_code == 200
                assert next(subscriber.iter_lines()) == 'id: 1'
                # Close a real TCP subscription while the Run is still queued.
            assert owner.read_events(sid, rid)['status'] == 'queued'
            result = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert owner.read_events(sid, rid)['status'] == 'complete'
            assert len(client.get(f'/api/chat/sessions/{sid}').json()['messages']) == 2
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.asyncio
async def test_event_write_failure_closes_generator_and_releases_worker_capacity(queue_setup, tmp_path, monkeypatch):
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
    def broken_append(*args, **kwargs):
        raise RuntimeError('Synthetic event write failure')
    monkeypatch.setattr(DurableChatRepository, 'append_event', broken_append)
    worker = ChatWorker(bundle, SecretStore(path=tmp_path/'unused.key'), {
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential, job: Gateway())
    assert await worker.run_once()
    assert worker.runtime.active == worker.runtime.waiting == 0
    assert worker.runtime.finishers == worker.runtime.tokens == {}
    result = owner.read_events(sid,rid)
    assert result['status'] == 'error'
    assert result['events'][-1]['data']['code'] == 'worker_error'
    assert len(owner.history(sid)) == 1


@pytest.mark.parametrize('column,value', [('event_sequence',1),('execution_mode','queued')])
def test_import_replay_rejects_planted_durable_run_fields(postgres_database, snapshot, column, value):
    import sqlite3
    from contextlib import closing
    from app.persistence.engine import build_engine
    from app.persistence.legacy_import import import_snapshot, ImportValidationError
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        with closing(sqlite3.connect(snapshot)) as source, source:
            source.execute("INSERT INTO agent_runs(id,session_id,status,provider,model,started_at) "
                "SELECT ?,id,'interrupted','deepseek','synthetic',created_at FROM chat_sessions", (str(uuid.uuid4()),))
        import_snapshot(snapshot,engine)
        from app.persistence.schema import metadata
        with engine.begin() as db:
            db.execute(metadata.tables['agent_runs'].update().values(**{column:value}))
        with pytest.raises(ImportValidationError,match='durable run'):
            import_snapshot(snapshot,engine)
    finally:
        engine.dispose()


def test_final_answer_events_replace_draft_and_chunk_unicode_atomically(queue_setup):
    bundle,root,owner,tokens=queue_setup
    sid,rid=submit(owner)
    job=root.claim_next()
    owned=root.authenticated(job['credential']).with_lease(job['lease'])
    owned.append_event(rid,'token',{'text':'discard this earlier draft'})
    content='退货🙂'*3000
    assert owned.complete_run(run_id=rid,session_id=sid,message_id=str(uuid.uuid4()),content=content,
        provider='deepseek',model='synthetic',citations=[],now=datetime.now(timezone.utc).isoformat())
    events=owner.read_events(sid,rid)['events']
    reset=next(index for index,event in enumerate(events) if event['event']=='answer_reset')
    chunks=[event['data']['text'] for event in events[reset+1:] if event['event']=='token']
    assert len(chunks)==2 and ''.join(chunks)==content
    assert all(len(value.encode('utf-8'))<262144 for value in chunks)
    assert events[-1]['event']=='done'
    assert owner.snapshot(sid,rid)['run']['draft']==content


def test_snapshot_cursor_draft_reset_completion_and_scope(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    job = root.claim_next()
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    owned.append_event(rid,'token',{'text':'discarded'})
    owned.append_event(rid,'answer_reset',{})
    owned.append_event(rid,'token',{'text':'retained'})
    snapshot = owner.snapshot(sid)
    assert snapshot['run']['draft'] == 'retained'
    assert snapshot['run']['cursor'] == 5
    assert [m['role'] for m in snapshot['messages']] == ['user']
    assert root.authenticated(tokens['synthetic.other']).snapshot(sid) is None
    assert owner.snapshot(sid,str(uuid.uuid4())) is None
    owned.append_event(rid,'token',{'text':' tail'})
    assert [e['data']['text'] for e in owner.read_events(sid,rid,after=snapshot['run']['cursor'])['events']] == [' tail']
    mid = str(uuid.uuid4())
    assert owned.complete_run(run_id=rid,session_id=sid,message_id=mid,content='retained tail',provider='deepseek',model='synthetic',
        citations=[{'synthetic':True}],contact_summary='Synthetic contact',now=datetime.now(timezone.utc).isoformat())
    assert owner.snapshot(sid)['run'] is None
    result = owner.snapshot(sid,rid)
    assert result['run']['status'] == 'complete'
    assert result['run']['message_id'] == result['messages'][-1]['id'] == mid
    assert result['run']['citations'] == result['messages'][-1]['citations'] == [{'synthetic':True}]
    assert result['run']['contact_summary'] == 'Synthetic contact'
    assert not owner.request_cancel(sid,rid)
    foreign = bundle.identity.create_tenant('Synthetic other snapshot tenant')
    bundle.identity.provision_user('synthetic.foreign','synthetic-password-only',foreign,role='admin')
    foreign_token = bundle.identity.login('synthetic.foreign','synthetic-password-only',foreign).token
    assert root.authenticated(foreign_token).snapshot(sid,rid) is None
    bundle.identity.logout(tokens['synthetic.customer'])
    with pytest.raises(AuthenticationError):
        owner.snapshot(sid,rid)


def test_snapshot_waits_for_session_transaction_not_a_torn_history(queue_setup):
    import threading
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    acquired = threading.Event()
    def read():
        acquired.set()
        return owner.snapshot(sid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with bundle.engine.begin() as db:
            root._lock_session(db,sid)
            future = pool.submit(read)
            assert acquired.wait(2)
            time.sleep(.1)
            assert not future.done()
        result = future.result(timeout=5)
    assert result['run']['cursor'] == 2 and len(result['messages']) == 1


def test_event_total_budget_preserves_terminal_reserve(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    job = root.claim_next()
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    for _ in range(13):
        owned.append_event(rid,'token',{'text':'x'*240000})
    with pytest.raises(ValueError,match='budget'):
        owned.append_event(rid,'token',{'text':'x'*240000})
    assert owner.snapshot(sid)['run']['cursor'] == 15
    assert owned.finish_run(rid,'error','event_budget',datetime.now(timezone.utc).isoformat())
    result = owner.snapshot(sid,rid)
    assert result['run']['status'] == 'error' and result['run']['draft'] == ''
    assert result['run']['cursor'] == 17


def test_terminal_budget_failure_rolls_back_answer_and_terminal_events(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    job = root.claim_next()
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    for _ in range(13):
        owned.append_event(rid,'token',{'text':'x'*240000})
    with pytest.raises(ValueError,match='budget'):
        owned.complete_run(run_id=rid,session_id=sid,message_id=str(uuid.uuid4()),content='Synthetic answer',
            provider='deepseek',model='synthetic',citations=[{'excerpt':'y'*240000}]*5,
            now=datetime.now(timezone.utc).isoformat())
    result = owner.snapshot(sid,rid)
    assert len(result['messages']) == 1
    assert result['run']['status'] == 'running' and result['run']['cursor'] == 15
    assert owned.finish_run(rid,'error','event_budget',datetime.now(timezone.utc).isoformat())


def test_separate_silent_worker_honors_persistent_cancel_and_resets_draft(queue_setup, postgres_database, tmp_path):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    process = subprocess.Popen(worker_command(postgres_database,tmp_path,delay=60),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+20
        # --once deliberately exits when every candidate is busy. Reading the
        # public event snapshot takes the same session lock as claim_next; do
        # not let the test observer turn a ready job into a busy candidate.
        def observed_sequence():
            r = root.tables['agent_runs']
            with bundle.engine.connect() as db:
                return db.execute(sa.select(r.c.event_sequence).where(r.c.id == uuid.UUID(rid))).scalar_one()
        while time.monotonic() < deadline and observed_sequence() < 4:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail(f'Silent Worker exited before draft: status={owner.snapshot(sid,rid)["run"]}, '
                            f'stdout={stdout.decode(errors="replace")}, stderr={stderr.decode(errors="replace")}')
            time.sleep(.05)
        snapshot = owner.snapshot(sid,rid)
        assert snapshot['run']['status'] == 'running' and snapshot['run']['draft']
        assert owner.request_cancel(sid,rid)
        assert owner.snapshot(sid,rid)['run']['cancel_requested']
        process.communicate(timeout=8)
        assert process.returncode == 0
        result = owner.snapshot(sid,rid)
        assert result['run']['status'] == 'interrupted' and result['run']['draft'] == ''
        assert len(result['messages']) == 1
        assert owner.read_events(sid,rid)['events'][-1]['event'] == 'interrupted'
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_once_worker_skips_busy_session_then_claims_after_unlock(queue_setup, postgres_database, tmp_path):
    bundle, root, owner, tokens = queue_setup
    sid, rid = submit(owner)
    with bundle.engine.begin() as db:
        root._lock_session(db, sid)
        process = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, timeout=20)
        assert process.returncode == 0, process.stderr.decode(errors='replace')
    assert owner.snapshot(sid, rid)['run']['status'] == 'queued'
    process = subprocess.run(worker_command(postgres_database, tmp_path), capture_output=True, timeout=20)
    assert process.returncode == 0, process.stderr.decode(errors='replace')
    assert owner.snapshot(sid, rid)['run']['status'] == 'complete'
