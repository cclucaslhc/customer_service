"""Terminal snapshots/pruning on disposable PostgreSQL, never real user records."""
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.identity import AuthorizationError
from app.persistence.durable_chat_repository import DurableChatRepository, DurableChatConflict, EventCursorExpired
from app.persistence.schema import metadata
from test_durable_chat import queue_setup, submit
from test_postgresql_integration import postgres_database

COMPLETE_EVENTS = 8


def complete(root, owner, **kwargs):
    sid,rid = submit(owner,**kwargs)
    job = root.claim_next()
    assert job['run_id'] == rid
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    owned.append_event(rid,'token',{'text':'Synthetic terminal answer'})
    assert owned.complete_run(run_id=rid,session_id=sid,message_id=str(uuid.uuid4()),content='Synthetic terminal answer',
        provider='deepseek',model='synthetic',citations=[{'synthetic':True}],contact_summary='Synthetic summary',
        now=datetime.now(timezone.utc).isoformat())
    events=owner.read_events(sid,rid)['events']
    assert [item['event'] for item in events] == [
        'run_started','run_queued','token','answer_reset','token','citation','contact_form','done']
    assert [item['sequence'] for item in events] == list(range(1,COMPLETE_EVENTS+1))
    return sid,rid


def age(bundle, rid):
    runs = metadata.tables['agent_runs']
    with bundle.engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
            finished_at=sa.func.clock_timestamp()-sa.text("interval '2 days'")))


def test_prune_preserves_final_snapshot_counters_and_idempotent_command(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    before = owner.snapshot(sid,rid)
    assert root.prune_events(retention_seconds=86400) == {'runs':0,'events':0}
    runs = metadata.tables['agent_runs']
    with bundle.engine.connect() as db:
        counter = db.execute(sa.select(runs.c.event_data_bytes).where(runs.c.id == uuid.UUID(rid))).scalar_one()
        actual = db.execute(sa.select(sa.func.sum(sa.func.octet_length(sa.cast(metadata.tables['chat_run_events'].c.data,sa.Text()))))
            .where(metadata.tables['chat_run_events'].c.run_id == uuid.UUID(rid))).scalar_one()
        assert counter == actual > 0
    age(bundle,rid)
    assert root.prune_events(retention_seconds=86400) == {'runs':1,'events':COMPLETE_EVENTS}
    result = owner.snapshot(sid,rid)
    assert result['run']['draft'] == before['run']['draft']
    assert result['run']['message_id'] == result['messages'][-1]['id']
    assert result['run']['contact_summary'] == 'Synthetic summary'
    assert result['run']['citations'] == [{'synthetic':True}]
    assert result['run']['event_floor'] == result['run']['cursor'] == COMPLETE_EVENTS
    with pytest.raises(EventCursorExpired):
        owner.read_events(sid,rid)
    assert root.authenticated(tokens['synthetic.other']).read_events(sid,rid) is None
    assert owner.read_events(sid,rid,after=COMPLETE_EVENTS)['events'] == []
    assert submit(owner,sid=sid,rid=rid) == (sid,rid)
    assert root.claim_next() is None and len(owner.history(sid)) == 2
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(runs.c.event_data_bytes).where(runs.c.id == uuid.UUID(rid))).scalar_one() == counter
    assert root.prune_events(retention_seconds=86400) == {'runs':0,'events':0}


def test_prune_failure_snapshot_and_cancel_result_remain_queryable(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = submit(owner)
    assert owner.request_cancel(sid,rid)
    assert root.reap_pending() == 1
    age(bundle,rid)
    assert root.prune_events(retention_seconds=86400)['runs'] == 1
    result = owner.snapshot(sid,rid)
    assert result['run']['status'] == 'interrupted' and result['run']['cancel_requested']
    assert result['run']['draft'] == '' and result['run']['message_id'] is None
    assert len(result['messages']) == 1


def test_parallel_bounded_cleanup_never_prunes_live_run(queue_setup):
    bundle,root,owner,tokens = queue_setup
    finished = [complete(root,owner) for _ in range(3)]
    for _,rid in finished:
        age(bundle,rid)
    sid,live = submit(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: root.prune_events(retention_seconds=86400,limit=1),range(2)))
    assert 1 <= sum(r['runs'] for r in results) <= 2
    assert sum(r['events'] for r in results) == COMPLETE_EVENTS*sum(r['runs'] for r in results)
    remaining = root.prune_events(retention_seconds=86400)
    assert remaining['runs']+sum(r['runs'] for r in results) == 3
    assert owner.snapshot(sid,live)['run']['event_floor'] == 0
    assert owner.read_events(sid,live)['status'] == 'queued'


def test_cleanup_skips_locked_run_and_busy_session(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    with bundle.engine.begin() as db:
        root._lock_session(db,sid)
        assert root.prune_events(retention_seconds=86400)['runs'] == 0
    runs = metadata.tables['agent_runs']
    with bundle.engine.begin() as db:
        db.execute(sa.select(runs.c.id).where(runs.c.id == uuid.UUID(rid)).with_for_update())
        assert root.prune_events(retention_seconds=86400)['runs'] == 0
    assert root.prune_events(retention_seconds=86400)['runs'] == 1


def test_cleanup_delete_and_watermark_are_atomic(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    def fail_watermark(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('UPDATE agent_runs') and 'event_floor=' in statement:
            raise RuntimeError('Synthetic failure after DELETE')
    sa.event.listen(bundle.engine,'before_cursor_execute',fail_watermark)
    try:
        with pytest.raises(RuntimeError,match='Synthetic failure'):
            root.prune_events(retention_seconds=86400)
    finally:
        sa.event.remove(bundle.engine,'before_cursor_execute',fail_watermark)
    assert owner.snapshot(sid,rid)['run']['event_floor'] == 0
    assert len(owner.read_events(sid,rid)['events']) == COMPLETE_EVENTS
    assert root.prune_events(retention_seconds=86400)['runs'] == 1


def test_inconsistent_snapshot_blocks_cleanup_and_user_cannot_enable_pruning(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    snapshots = metadata.tables['chat_run_snapshots']
    with bundle.engine.begin() as db:
        db.execute(snapshots.update().where(snapshots.c.run_id == uuid.UUID(rid)).values(sequence=5))
    assert root.prune_events(retention_seconds=86400)['runs'] == 0
    assert len(owner.read_events(sid,rid)['events']) == COMPLETE_EVENTS
    with pytest.raises(DurableChatConflict):
        owner.snapshot(sid,rid)
    with pytest.raises(AuthorizationError):
        owner.prune_events(retention_seconds=86400)
    for seconds in (None,True,0,60,86400.0,91*86400):
        with pytest.raises(ValueError):
            root.prune_events(retention_seconds=seconds)


def test_retention_cli_requires_policy_and_emits_only_counts(queue_setup, postgres_database):
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    result = subprocess.run([sys.executable,'scripts/prune_chat_events.py','--database-url',postgres_database,
        '--retention-seconds','86400','--limit','1'],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f'Pruned Runs: 1; events: {COMPLETE_EVENTS}'
    assert owner.snapshot(sid,rid)['run']['event_floor'] == COMPLETE_EVENTS


def test_0010_upgrade_preserves_result_and_backfills_exact_counters(postgres_database):
    from app.persistence.engine import build_engine
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config('alembic.ini'),'0010_durable_chat')
    engine = build_engine(postgres_database,pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant('Synthetic old snapshot source')
        uid = identity.provision_user('synthetic.old','synthetic-password-only',tenant)
        token = identity.login('synthetic.old','synthetic-password-only',tenant).token
        # Seed historical schema through reflection, never current repositories.
        old = sa.MetaData()
        old.reflect(engine,only=['chat_sessions','agent_runs','messages','chat_run_events'])
        sid,rid,mid = uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        now = datetime.now(timezone.utc)
        with engine.begin() as db:
            db.execute(old.tables['chat_sessions'].insert().values(id=sid,tenant_id=uuid.UUID(tenant),owner_id=uuid.UUID(uid),
                title='Synthetic historical',created_at=now,updated_at=now))
            db.execute(old.tables['messages'].insert().values(id=mid,tenant_id=uuid.UUID(tenant),session_id=sid,
                sequence=1,role='assistant',content='Authoritative final answer',citations_json=[{'historical':True}],created_at=now))
            db.execute(old.tables['agent_runs'].insert().values(id=rid,tenant_id=uuid.UUID(tenant),session_id=sid,
                status='complete',execution_mode='queued',event_sequence=3,provider='deepseek',model='synthetic',started_at=now,finished_at=now))
            db.execute(old.tables['chat_run_events'].insert(),[dict(tenant_id=uuid.UUID(tenant),run_id=rid,sequence=i,event=kind,data=data,created_at=now)
                for i,kind,data in [(1,'token',{'text':'stale draft'}),(2,'contact_form',{'summary':'Historical summary'}),
                    (3,'done',{'message_id':str(mid),'run_id':str(rid)})]])
        command.upgrade(Config('alembic.ini'),'head')
        root = DurableChatRepository(engine)
        result = root.authenticated(token).snapshot(str(sid),str(rid))
        assert result['run']['draft'] == result['messages'][0]['content'] == 'Authoritative final answer'
        assert result['run']['citations'] == [{'historical':True}]
        assert result['run']['contact_summary'] == 'Historical summary'
        with engine.connect() as db:
            expected = db.execute(sa.text('SELECT sum(octet_length(data::text)) FROM chat_run_events')).scalar_one()
            assert db.execute(sa.select(metadata.tables['agent_runs'].c.event_data_bytes)).scalar_one() == expected
        age(type('SyntheticBundle',(),{'engine':engine})(),str(rid))
        assert root.prune_events(retention_seconds=86400)['events'] == 3
        assert root.authenticated(token).snapshot(str(sid),str(rid))['run']['draft'] == 'Authoritative final answer'
    finally:
        engine.dispose()


def test_0011_upgrade_rejects_missing_final_message_and_rolls_back(postgres_database):
    from app.persistence.engine import build_engine
    command.upgrade(Config('alembic.ini'),'0010_durable_chat')
    engine = build_engine(postgres_database,pool_size=1)
    sid,rid = uuid.uuid4(),uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        old = sa.MetaData()
        old.reflect(engine,only=['chat_sessions','agent_runs','chat_run_events'])
        with engine.begin() as db:
            db.execute(old.tables['chat_sessions'].insert().values(id=sid,title='Synthetic corrupt source',created_at=now,updated_at=now))
            db.execute(old.tables['agent_runs'].insert().values(id=rid,session_id=sid,status='complete',execution_mode='queued',
                event_sequence=1,provider='deepseek',model='synthetic',started_at=now,finished_at=now))
            from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
            db.execute(old.tables['chat_run_events'].insert().values(tenant_id=uuid.UUID(LEGACY_TENANT_ID),run_id=rid,
                sequence=1,event='done',data={'message_id':str(uuid.uuid4())},created_at=now))
        with pytest.raises(sa.exc.DatabaseError,match='authoritative assistant message'):
            command.upgrade(Config('alembic.ini'),'head')
        inspector = sa.inspect(engine)
        assert 'chat_run_snapshots' not in inspector.get_table_names()
        assert 'event_data_bytes' not in {col['name'] for col in inspector.get_columns('agent_runs')}
        with engine.connect() as db:
            assert db.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '0010_durable_chat'
            assert db.execute(sa.text('SELECT count(*) FROM chat_run_events')).scalar_one() == 1
    finally:
        engine.dispose()


def test_http_expired_cursor_returns_authorized_snapshot_not_other_users_data(queue_setup,tmp_path):
    from fastapi.testclient import TestClient
    from app.auth_http import AuthPolicy
    from app.main import create_app
    from app.security import SecretStore
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle,auth_policy=policy,durable_chat=True,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'})
    tenant = bundle.identity.resolve(tokens['synthetic.customer']).tenant_id
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        def login(name):
            client.cookies.clear()
            result = client.post('/api/auth/login',headers={'Origin':policy.origin},json={
                'username':name,'password':'synthetic-password-only','tenant_id':tenant})
            assert result.status_code == 200
        login('synthetic.customer')
        url = f'/api/chat/sessions/{sid}/runs/{rid}'
        # Retention is off by default, even for old terminal Runs.
        assert 'event: done' in client.get(url+'/events').text
        assert root.prune_events(retention_seconds=86400)['runs'] == 1
        response = client.get(url+'/events')
        assert response.status_code == 409 and response.json()['code'] == 'cursor_expired'
        assert client.get(response.json()['snapshot_url']).json()['run']['draft'] == 'Synthetic terminal answer'
        assert client.get(url+f'/events?after={COMPLETE_EVENTS}').status_code == 200
        login('synthetic.other')
        assert client.get(url+'/events').status_code == client.get(url).status_code == 404
    assert not (tmp_path/'unused.key').exists()


@pytest.mark.parametrize('enabled',[False,True])
def test_lifespan_retention_only_when_policy_explicitly_enabled(queue_setup,tmp_path,enabled):
    import time
    from fastapi.testclient import TestClient
    from app.auth_http import AuthPolicy
    from app.main import create_app
    from app.security import SecretStore
    bundle,root,owner,tokens = queue_setup
    sid,rid = complete(root,owner)
    age(bundle,rid)
    app = create_app(repositories=bundle,auth_policy=AuthPolicy('https://localhost'),durable_chat=True,
        event_retention_seconds=86400 if enabled else None,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'})
    runs = metadata.tables['agent_runs']
    with TestClient(app,base_url='https://localhost',client=('127.0.0.1',50000)) as client:
        assert client.get('/health').status_code == 200
        deadline = time.monotonic()+3
        while True:
            with bundle.engine.connect() as db:
                floor = db.execute(sa.select(runs.c.event_floor).where(runs.c.id == uuid.UUID(rid))).scalar_one()
            if not enabled or floor or time.monotonic() >= deadline:
                break
            time.sleep(.05)
        assert floor == (COMPLETE_EVENTS if enabled else 0)
