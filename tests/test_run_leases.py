"""Fenced execution invariants on disposable PostgreSQL, never real model calls."""

import uuid
import asyncio
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.database import utc_now
from app.identity import AuthenticationError
from app.persistence.chat_repository import ChatRepository
from app.persistence.engine import build_engine
from app.persistence.identity_repository import IdentityRepository
from app.persistence.schema import metadata
from app.run_leases import RunLease, RunLeaseLost, RunCancellationRequested
from test_postgresql_integration import postgres_database
from test_production_migration import snapshot


def test_uuid_session_locks_match_across_http_case_and_cleanup_uuid_objects():
    sid = uuid.UUID('abcdefab-cdef-abcd-efab-cdefabcdefab')
    assert ChatRepository._session_lock_key(sid) == ChatRepository._session_lock_key(str(sid).upper())
    assert ChatRepository._session_lock_key('legacy-session') == ChatRepository._session_lock_key('legacy-session')


@pytest.fixture
def leased_database(postgres_database):
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=3)
    yield engine
    engine.dispose()


def start(repo, session_id=None, *, leased=True):
    sid, rid, owner = session_id or str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    assert repo.start_run(session_id=sid, run_id=rid, user_id=str(uuid.uuid4()), title='Synthetic lease',
        content='Synthetic question', provider='deepseek', model='synthetic', prompt_version_id=None, now=utc_now(),
        **({'lease_owner': owner} if leased else {}))
    return sid, rid, repo.with_lease(RunLease(rid, owner, 1)) if leased else repo


def complete(repo, sid, rid):
    return repo.complete_run(run_id=rid, session_id=sid, message_id=str(uuid.uuid4()), content='Synthetic answer',
        provider='deepseek', model='synthetic', citations=[], now=utc_now())


def expire(engine, rid):
    runs = metadata.tables['agent_runs']
    with engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(
            lease_until=sa.func.clock_timestamp() - sa.text("INTERVAL '1 second'")))


def test_owned_run_rejects_unfenced_wrong_owner_and_expired_writes(leased_database):
    repo = ChatRepository(leased_database)
    sid, rid, owned = start(repo)
    assert owned.renew_lease()
    assert not complete(repo, sid, rid)
    forged = repo.with_lease(RunLease(rid, str(uuid.uuid4()), 1))
    assert not forged.renew_lease() and not complete(forged, sid, rid)
    with pytest.raises(RunLeaseLost):
        forged.start_tool(rid, 'synthetic', utc_now())
    tool = owned.start_tool(rid, 'synthetic', utc_now())
    expire(leased_database, rid)
    assert not owned.renew_lease()
    assert not owned.finish_tool(tool, utc_now())
    assert not complete(owned, sid, rid)
    assert not owned.finish_run(rid, 'interrupted', 'late', utc_now())
    assert len(repo.history(sid)) == 1
    assert repo.reap_expired_leases() == 1
    assert repo.reap_expired_leases() == 0
    with leased_database.connect() as db:
        run = db.execute(sa.select(metadata.tables['agent_runs'])).mappings().one()
        assert run['status'] == 'interrupted' and run['error_code'] == 'lease_expired' and run['lease_fence'] == 2
        assert db.execute(sa.select(metadata.tables['agent_run_tools'].c.status)).scalar_one() == 'interrupted'
    assert repo.session(sid)['status'] == 'idle'


def test_cancel_is_durable_cross_connection_and_result_cannot_commit(leased_database, postgres_database):
    engine_b = build_engine(postgres_database, pool_size=1)
    try:
        a, b = ChatRepository(leased_database), ChatRepository(engine_b)
        sid, rid, owned = start(a)
        assert b.request_cancel(sid, str(uuid.uuid4())) is False
        assert b.request_cancel(sid, rid) is True
        assert b.request_cancel(sid, rid) is True
        assert not owned.renew_lease() and not complete(owned, sid, rid)
        with pytest.raises(RunCancellationRequested):
            owned.renew_lease(raise_on_cancel=True)
        forged = a.with_lease(RunLease(rid,str(uuid.uuid4()),1))
        assert not forged.renew_lease(raise_on_cancel=True)
        assert owned.finish_run(rid, 'interrupted', 'cancelled', utc_now())
        assert b.request_cancel(sid, rid) is False
        assert len(a.history(sid)) == 1
    finally:
        engine_b.dispose()


def test_concurrent_preemption_one_live_run_and_late_tool_results_rejected(leased_database):
    repo = ChatRepository(leased_database)
    sid, old_id, old = start(repo)
    tool = old.start_tool(old_id, 'synthetic', utc_now())
    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(lambda _: start(repo, sid), range(6)))
    with leased_database.connect() as db:
        table = metadata.tables['agent_runs']
        live = str(db.execute(sa.select(table.c.id).where(table.c.status == 'running')).scalar_one())
    assert not old.finish_tool(tool, utc_now()) and not complete(old, sid, old_id)
    for _, rid, owned in runs:
        assert complete(owned, sid, rid) == (rid == live)
    assert len(repo.history(sid)) == 8  # seven questions and the sole surviving answer
    assert repo.session(sid)['status'] == 'idle'


def test_expiry_cleanup_is_bounded_concurrent_and_ignores_live_or_unleased(leased_database, postgres_database):
    repo = ChatRepository(leased_database)
    _, dead, _ = start(repo)
    _, other_dead, _ = start(repo)
    live_sid, live, owned = start(repo)
    legacy_sid, legacy, _ = start(repo, leased=False)
    expire(leased_database, dead)
    expire(leased_database, other_dead)
    assert repo.reap_expired_leases(limit=1) == 1
    engine_b = build_engine(postgres_database, pool_size=1)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(item.reap_expired_leases) for item in (repo, ChatRepository(engine_b))]
            assert sum(f.result() for f in futures) == 1
    finally:
        engine_b.dispose()
    assert owned.renew_lease() and complete(owned, live_sid, live)
    assert repo.session(legacy_sid)['status'] == 'running'
    assert repo.finish_run(legacy, 'interrupted', 'synthetic', utc_now())
    with pytest.raises(ValueError):
        repo.reap_expired_leases(limit=101)


def test_expiry_between_completion_checks_never_commits_answer(leased_database, monkeypatch):
    repo = ChatRepository(leased_database)
    sid, rid, owned = start(repo)
    checks = iter((True, False))
    monkeypatch.setattr(owned, '_lease_current', lambda *args, **kwargs: next(checks))
    assert not complete(owned, sid, rid)
    assert len(repo.history(sid)) == 1
    assert repo.session(sid)['status'] == 'running'


def test_cleanup_skips_locked_run_and_busy_session_without_blocking(leased_database):
    repo = ChatRepository(leased_database)
    sid, rid, _ = start(repo)
    expire(leased_database, rid)
    runs = metadata.tables['agent_runs']
    with leased_database.begin() as db:
        db.execute(sa.select(runs.c.id).where(runs.c.id == uuid.UUID(rid)).with_for_update()).one()
        assert repo.reap_expired_leases() == 0
    with leased_database.begin() as db:
        repo._lock_session(db, sid)
        assert repo.reap_expired_leases() == 0
    assert repo.reap_expired_leases() == 1


def test_imported_legacy_runs_remain_unleased_and_replay_checks_new_fields(leased_database, snapshot):
    from app.persistence.legacy_import import import_snapshot, ImportValidationError
    rid = str(uuid.uuid4())
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("INSERT INTO agent_runs(id,session_id,status,started_at) "
            "SELECT ?,id,'running',created_at FROM chat_sessions", (rid,))
    assert import_snapshot(snapshot, leased_database)['status'] == 'imported'
    assert import_snapshot(snapshot, leased_database)['status'] == 'already_imported'
    assert ChatRepository(leased_database).reap_expired_leases() == 0
    runs = metadata.tables['agent_runs']
    with leased_database.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(rid)).values(lease_owner=uuid.uuid4(),
            lease_fence=1, lease_until=sa.func.clock_timestamp() + sa.text("INTERVAL '30 seconds'")))
    with pytest.raises(ImportValidationError, match='run lease'):
        import_snapshot(snapshot, leased_database)


def test_lease_completion_insert_failure_rolls_back_terminal_state(leased_database, monkeypatch):
    repo = ChatRepository(leased_database)
    sid, rid, owned = start(repo)
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic insert fault')
    monkeypatch.setattr(owned, '_insert_message', fail)
    with pytest.raises(RuntimeError, match='Synthetic insert fault'):
        complete(owned, sid, rid)
    assert owned.renew_lease()
    assert repo.session(sid)['status'] == 'running' and len(repo.history(sid)) == 1


def test_cancel_scope_revocation_and_internal_expiry_cleanup(leased_database):
    identity = IdentityRepository(leased_database)
    tenant = identity.create_tenant('Synthetic lease identity')
    tokens = {}
    for name in ('synthetic.owner', 'synthetic.other'):
        identity.provision_user(name, 'synthetic-password-only', tenant)
        tokens[name] = identity.login(name, 'synthetic-password-only', tenant).token
    root = ChatRepository(leased_database)
    a = root.authenticated(tokens['synthetic.owner'])
    b = root.authenticated(tokens['synthetic.other'])
    sid, rid, owned = start(a)
    assert not b.request_cancel(sid, rid)
    assert owned.renew_lease()
    with pytest.raises(ValueError):
        a.reap_expired_leases()
    identity.logout(tokens['synthetic.owner'])
    with pytest.raises(AuthenticationError):
        owned.renew_lease()
    with pytest.raises(AuthenticationError):
        owned.finish_run(rid, 'interrupted', 'revoked', utc_now())
    expire(leased_database, rid)
    assert root.reap_expired_leases() == 1
    with leased_database.connect() as db:
        assert db.execute(sa.select(metadata.tables['agent_runs'].c.error_code)).scalar_one() == 'lease_expired'


@pytest.mark.asyncio
async def test_independent_runtimes_cancel_silent_model_without_shared_token(leased_database, postgres_database):
    from types import SimpleNamespace
    from pydantic_ai.models.function import FunctionModel
    from app.chat import ChatOrchestrator
    identity = IdentityRepository(leased_database)
    tenant = identity.create_tenant('Synthetic independent runtimes')
    identity.provision_user('synthetic.stream', 'synthetic-password-only', tenant)
    login = identity.login('synthetic.stream', 'synthetic-password-only', tenant)
    engine_b = build_engine(postgres_database, pool_size=1)
    started = asyncio.Event()
    async def silent(messages, info):
        started.set()
        await asyncio.Event().wait()
        yield 'Must never be emitted'
    class Gateway:
        def snapshot(self):
            return SimpleNamespace(provider='deepseek', model='synthetic')
        def build_model(self, snapshot):
            return FunctionModel(stream_function=silent)
    try:
        def view(engine):
            return ChatOrchestrator(Gateway(), SimpleNamespace(),
                repository=ChatRepository(engine).authenticated(login.token),
                identity_resolver=lambda: identity.resolve(login.token),
                prompt_resolver=lambda: (None, 'Synthetic test rules'), heartbeat_interval=30)
        a, b = view(leased_database), view(engine_b)
        assert a.runtime is not b.runtime
        sid = str(uuid.uuid4())
        stream = a.stream(sid, 'Synthetic silent model')
        assert 'run_started' in await anext(stream)
        rid = a.session_runs[sid]
        assert b.tokens == {} and b.session_runs == {}
        async def collect():
            return ''.join([frame async for frame in stream])
        consumer = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert await asyncio.to_thread(b.cancel, sid, rid)
            frames = await asyncio.wait_for(consumer, timeout=6)
            assert 'event: interrupted' in frames and 'event: done' not in frames
            assert a.tokens == {} and a.runtime.finishers == {} and a.capacity()['active'] == 0
            assert len(a.history(sid)) == 1
            runs = metadata.tables['agent_runs']
            with leased_database.connect() as db:
                assert db.execute(sa.select(runs.c.status).where(runs.c.id == uuid.UUID(rid))).scalar_one() == 'interrupted'
        finally:
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await stream.aclose()
    finally:
        engine_b.dispose()
