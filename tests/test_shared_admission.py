"""Policy/transport failures plus actual PG global claim boundaries."""
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest
import sqlalchemy as sa
from app.admission import AdmissionPolicy, RedisAdmission, CoordinationUnavailable
from app.persistence.admission_repository import configure_policy
from app.persistence.schema import metadata
from test_durable_chat import queue_setup, submit
from test_postgresql_integration import postgres_database


def policy(capacity=2,rate=10):
    return AdmissionPolicy(str(uuid.uuid4()),capacity,rate)


def test_policy_and_fail_closed_transport_do_not_expose_credentials():
    with pytest.raises(ValueError):
        AdmissionPolicy(str(uuid.uuid4()),True,10)
    with pytest.raises(ValueError):
        AdmissionPolicy('not-a-uuid',1,10)
    class Broken:
        def eval(self,*args):
            raise RuntimeError('synthetic-private-transport-detail')
    adapter = RedisAdmission(Broken(),policy())
    with pytest.raises(CoordinationUnavailable) as exc:
        adapter.acquire()
    assert 'synthetic-private' not in str(exc.value)
    assert len(adapter.policy.fingerprint) == 64
    assert all('{'+adapter.policy.namespace+'}' in key for key in adapter.keys)


def test_postgres_serializes_capacity_across_workers_and_blocks_bypass(queue_setup):
    bundle,root,owner,tokens = queue_setup
    selected = policy()
    configure_policy(bundle.engine,selected)
    jobs = [submit(owner) for _ in range(5)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _:root.claim_next(admission_policy=selected),range(5)))
    assert sum(job is not None for job in claimed) == 2
    with pytest.raises(CoordinationUnavailable):
        root.claim_next()
    with pytest.raises(CoordinationUnavailable):
        root.claim_next(admission_policy=policy(100))
    with pytest.raises(CoordinationUnavailable):
        owner.start_run(session_id=str(uuid.uuid4()),run_id=str(uuid.uuid4()),user_id=str(uuid.uuid4()),
            title='Synthetic',content='Synthetic',provider='deepseek',model='synthetic',prompt_version_id=None,
            now=datetime.now(timezone.utc).isoformat(),lease_owner=str(uuid.uuid4()))
    assert root.claim_next(admission_policy=selected) is None
    job = next(item for item in claimed if item)
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    assert owned.finish_run(job['run_id'],'interrupted','synthetic',datetime.now(timezone.utc).isoformat())
    assert root.release_admission(job)
    assert root.claim_next(admission_policy=selected) is not None


def test_postgres_rate_survives_completed_runs_and_cannot_change_policy(queue_setup):
    bundle,root,owner,tokens = queue_setup
    selected = policy(2,1)
    configure_policy(bundle.engine,selected)
    configure_policy(bundle.engine,selected)
    with pytest.raises(CoordinationUnavailable):
        configure_policy(bundle.engine,policy())
    sid,rid = submit(owner)
    job = root.claim_next(admission_policy=selected)
    root.authenticated(job['credential']).with_lease(job['lease']).finish_run(rid,'interrupted','synthetic',datetime.now(timezone.utc).isoformat())
    submit(owner)
    assert root.claim_next(admission_policy=selected) is None
    r = metadata.tables['agent_runs']
    with bundle.engine.begin() as db:
        db.execute(r.update().where(r.c.id == uuid.UUID(rid)).values(
            admission_started_at=sa.func.clock_timestamp()-sa.text("interval '61 seconds'")))
    assert root.claim_next(admission_policy=selected) is not None


def test_policy_provisioning_requires_drained_runs_and_workers_match(queue_setup,tmp_path):
    from app.chat_worker import ChatWorker
    from app.security import SecretStore
    bundle,root,owner,tokens = queue_setup
    selected = policy()
    sid,rid = submit(owner)
    with pytest.raises(CoordinationUnavailable):
        configure_policy(bundle.engine,selected)
    assert owner.request_cancel(sid,rid)
    assert root.reap_pending() == 1
    configure_policy(bundle.engine,selected)
    with pytest.raises(CoordinationUnavailable):
        ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{})
    assert not (tmp_path/'unused.key').exists()


def test_preemption_keeps_capacity_until_old_owner_acknowledges_exit(queue_setup):
    bundle,root,owner,tokens = queue_setup
    selected = policy(1,10)
    configure_policy(bundle.engine,selected)
    sid,rid = submit(owner)
    old = root.claim_next(admission_policy=selected)
    submit(owner,sid=sid)
    assert owner.snapshot(sid,rid)['run']['status'] == 'interrupted'
    assert root.claim_next(admission_policy=selected) is None
    altered = dict(old,lease=type(old['lease'])(rid,str(uuid.uuid4()),old['lease'].fence))
    assert not root.release_admission(altered)
    assert root.release_admission(old)
    assert root.claim_next(admission_policy=selected) is not None


@pytest.mark.asyncio
async def test_worker_transport_failure_leaves_command_queued_without_model(queue_setup,tmp_path):
    from app.chat_worker import ChatWorker
    from app.security import SecretStore
    bundle,root,owner,tokens = queue_setup
    selected = policy()
    configure_policy(bundle.engine,selected)
    sid,rid = submit(owner)
    class Broken:
        def eval(self,*args):
            raise RuntimeError('Injected transport failure; not an actual Redis server')
    def forbidden(*args):
        pytest.fail('No gateway before shared admission')
    worker = ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{},
        admission=RedisAdmission(Broken(),selected),gateway_factory=forbidden)
    with pytest.raises(CoordinationUnavailable):
        await worker.run_once()
    assert owner.snapshot(sid,rid)['run']['status'] == 'queued'
    assert len(owner.history(sid)) == 1 and not (tmp_path/'unused.key').exists()


@pytest.mark.asyncio
async def test_injected_coordination_loss_stops_silent_worker_and_releases_pg_reservation(queue_setup,tmp_path):
    """PG/Worker boundary injection; deliberately NOT claimed as Redis/Lua proof."""
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    from pydantic_ai.models.function import FunctionModel
    bundle,root,owner,tokens = queue_setup
    selected = policy(1)
    configure_policy(bundle.engine,selected)
    sid,rid = submit(owner)
    entered = asyncio.Event()
    class FaultCoordinator:
        policy = selected
        def acquire(self):
            return str(uuid.uuid4())
        def require(self,permit):
            if entered.is_set():
                raise CoordinationUnavailable('Injected failure')
        def release(self,*args,**kwargs):
            pass
    async def synthetic(messages,info):
        yield 'Synthetic draft'
        entered.set()
        await asyncio.sleep(30)
        yield 'Forbidden late answer'
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
        def build_model(self,snapshot):
            return FunctionModel(stream_function=synthetic)
    worker = ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential,job:Gateway(),admission=FaultCoordinator())
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(),timeout=10)
        assert await asyncio.wait_for(task,timeout=10)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    result = owner.snapshot(sid,rid)
    assert result['run']['status'] == 'interrupted'
    assert result['run']['error_code'] == 'coordination_unavailable'
    assert result['run']['draft'] == '' and len(result['messages']) == 1
    assert worker.runtime.active == 0 and worker.runtime.tokens == {}
    runs = metadata.tables['agent_runs']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(runs.c.admission_until).where(runs.c.id == uuid.UUID(rid))).scalar_one() is None
