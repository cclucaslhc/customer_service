"""Real Redis tests: explicit loopback endpoint, unique keys, never FLUSHDB."""
import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pytest
import sqlalchemy as sa
from app.admission import AdmissionPolicy, RedisAdmission, CoordinationUnavailable
from app.persistence.admission_repository import configure_policy
from app.persistence.schema import metadata
from test_durable_chat import queue_setup, submit
from test_postgresql_integration import postgres_database


def test_remote_redis_requires_tls_without_connecting():
    policy = AdmissionPolicy(str(uuid.uuid4()), 1, 1)
    with pytest.raises(ValueError, match='TLS'):
        RedisAdmission.from_url('redis://redis.example.test:6379/0',policy)
    local = RedisAdmission.from_url('redis://127.0.0.1:6379/0',policy)
    remote_tls = RedisAdmission.from_url('rediss://redis.example.test:6379/0',policy)
    local.close()
    remote_tls.close()


@pytest.fixture
def redis_factory():
    redis = pytest.importorskip('redis')
    from urllib.parse import urlsplit
    url = os.environ.get('TEST_REDIS_URL')
    if not url:
        pytest.skip('Set explicit TEST_REDIS_URL for disposable Redis integration')
    if urlsplit(url).hostname not in {'localhost','127.0.0.1','::1'}:
        pytest.fail('Redis integration requires an explicit loopback test endpoint')
    made = []
    def factory(capacity=2,rate=10,selected=None):
        adapter = RedisAdmission.from_url(url,selected or AdmissionPolicy(str(uuid.uuid4()),capacity,rate))
        adapter.client.ping()  # Unreachable configured infrastructure fails, not skips.
        made.append(adapter)
        return adapter
    try:
        yield factory
    finally:
        for adapter in made:
            try:
                adapter.client.delete(*adapter.keys)  # Only keys allocated here.
            finally:
                adapter.close()


def test_real_redis_atomic_capacity_rate_refund_and_expired_owner(redis_factory):
    a = redis_factory(2,2)
    b = redis_factory(selected=a.policy)
    with ThreadPoolExecutor(max_workers=8) as pool:
        owners = list(pool.map(lambda i:(a if i%2 else b).acquire(),range(8)))
    permits = [item for item in owners if item]
    assert len(permits) == 2
    a.require(permits[0])
    a.release(permits[0])
    assert b.acquire() is None  # Finishing does not refund a started rate token.
    b.release(permits[1],refund=True)
    fresh = a.acquire()
    assert fresh
    a.client.zadd(a.keys[1],{fresh:0})
    with pytest.raises(CoordinationUnavailable):
        a.require(fresh)
    a.release(fresh,refund=True)
    assert b.acquire()


def test_real_redis_mismatch_and_loss_never_renew_an_old_permit(redis_factory):
    a = redis_factory()
    owner = a.acquire()
    wrong = redis_factory(selected=AdmissionPolicy(a.policy.namespace,3,10))
    with pytest.raises(CoordinationUnavailable):
        wrong.acquire()
    a.client.delete(*a.keys)  # Simulated loss confined to this test namespace.
    with pytest.raises(CoordinationUnavailable):
        a.require(owner)
    assert a.acquire()


def test_redis_state_loss_does_not_bypass_postgres_capacity_or_rate(redis_factory,queue_setup):
    bundle,root,owner,tokens = queue_setup
    a = redis_factory(1,1)
    configure_policy(bundle.engine,a.policy)
    sid,rid = submit(owner)
    submit(owner)
    permit = a.acquire()
    job = root.claim_next(admission_policy=a.policy)
    a.client.delete(*a.keys)
    fresh = a.acquire()  # Redis alone has lost memory, PostgreSQL has not.
    assert fresh and root.claim_next(admission_policy=a.policy) is None
    owned = root.authenticated(job['credential']).with_lease(job['lease'])
    owned.finish_run(rid,'interrupted','synthetic',datetime.now(timezone.utc).isoformat())
    assert root.release_admission(job)
    assert root.claim_next(admission_policy=a.policy) is None  # Durable start rate remains.


@pytest.mark.asyncio
async def test_worker_stops_silent_model_on_coordination_loss(redis_factory,queue_setup,tmp_path):
    from pydantic_ai.models.function import FunctionModel
    from app.chat_worker import ChatWorker
    from app.providers import ProviderSnapshot
    from app.security import SecretStore
    bundle,root,owner,tokens = queue_setup
    a = redis_factory()
    configure_policy(bundle.engine,a.policy)
    sid,rid = submit(owner)
    entered = asyncio.Event()
    async def synthetic(messages,info):
        yield 'Synthetic draft'
        entered.set()
        await asyncio.sleep(30)
        yield 'Late answer must not save'
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
        def build_model(self,snapshot):
            return FunctionModel(stream_function=synthetic)
    worker = ChatWorker(bundle,SecretStore(path=tmp_path/'unused.key'),{
        'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        gateway_factory=lambda credential,job:Gateway(),admission=a)
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(),timeout=10)
        a.client.delete(*a.keys)
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
    assert not (tmp_path/'unused.key').exists()
