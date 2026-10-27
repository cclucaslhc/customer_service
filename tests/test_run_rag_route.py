"""Repository-level pin/fence proof, not yet automatic Worker integration."""
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
import sqlalchemy as sa
from app.database import utc_now
from app.knowledge_retrieval import KnowledgeIndexChanged
from app.persistence.schema import metadata
from app.persistence.durable_chat_repository import DurableChatRepository
from test_durable_chat import submit
from test_rag_rollout_service import staged_plan,planned,release,queue_setup,postgres_database


def claimed(bundle,token):
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(token))
    job=root.claim_next()
    return root.authenticated(job['credential']).with_lease(job['lease']),sid,rid


def complete(owner,sid,rid):
    return owner.complete_run(run_id=rid,session_id=sid,message_id=str(uuid.uuid4()),
        content='Synthetic routed answer',provider='deepseek',model='synthetic',citations=[],now=utc_now())


def test_pin_is_durable_idempotent_and_database_write_once(staged_plan):
    bundle,controller,_,start,tokens=staged_plan
    controller.start(**start)
    owner,sid,rid=claimed(bundle,tokens[0])
    route=owner.pin_rag_route(run_id=rid,session_id=sid)
    assert route['rollout_id']==start['rollout_id']
    assert owner.pin_rag_route(run_id=rid,session_id=sid)==route
    runs=metadata.tables['agent_runs']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(runs.c.rag_route).where(runs.c.id==uuid.UUID(rid))).scalar_one()==route
    for changed in (None,{**route,'route_version':100}):
        with pytest.raises(sa.exc.DBAPIError,match='immutable'),bundle.engine.begin() as db:
            db.execute(runs.update().where(runs.c.id==uuid.UUID(rid)).values(rag_route=changed))
    assert complete(owner,sid,rid)


def test_pause_fences_commit_even_if_caller_omits_index_pinned_flag(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    running=controller.start(**start)
    owner,sid,rid=claimed(bundle,tokens[0])
    owner.pin_rag_route(run_id=rid,session_id=sid)
    control['expected_rollout_version']=running['version']
    controller.pause(**control)
    with pytest.raises(KnowledgeIndexChanged):
        complete(owner,sid,rid)
    with pytest.raises(KnowledgeIndexChanged):
        owner.pin_rag_route(run_id=rid,session_id=sid)
    messages=metadata.tables['messages']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(messages).where(
            messages.c.session_id==uuid.UUID(sid),messages.c.role=='assistant')).scalar_one()==0


def test_starting_rollout_invalidates_preexisting_baseline_snapshot(staged_plan):
    bundle,controller,_,start,tokens=staged_plan
    owner,sid,rid=claimed(bundle,tokens[0])
    route=owner.pin_rag_route(run_id=rid,session_id=sid)
    assert route['rollout_id'] is None
    controller.start(**start)
    with pytest.raises(KnowledgeIndexChanged):
        complete(owner,sid,rid)


def test_foreign_session_cannot_be_pinned(staged_plan):
    bundle,_,_,_,tokens=staged_plan
    owner,sid,rid=claimed(bundle,tokens[0])
    with pytest.raises(PermissionError):
        owner.pin_rag_route(run_id=rid,session_id=str(uuid.uuid4()))


def test_approver_revocation_committed_first_blocks_waiting_answer(staged_plan):
    bundle,controller,_,start,tokens=staged_plan
    controller.start(**start)
    owner,sid,rid=claimed(bundle,tokens[0])
    owner.pin_rag_route(run_id=rid,session_id=sid)
    reviewer=bundle.identity.resolve(tokens[1])
    members=metadata.tables['identity_memberships']
    with bundle.engine.connect() as revoker:
        transaction=revoker.begin()
        pid=revoker.execute(sa.select(sa.func.pg_backend_pid())).scalar_one()
        revoker.execute(members.update().where(members.c.tenant_id==uuid.UUID(reviewer.tenant_id),
            members.c.user_id==uuid.UUID(reviewer.user_id)).values(active=False))
        with ThreadPoolExecutor(max_workers=1) as pool:
            answer=pool.submit(complete,owner,sid,rid)
            try:
                deadline=time.monotonic()+5
                with bundle.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                    while not observer.execute(sa.text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity '
                            'WHERE :pid=ANY(pg_blocking_pids(pid)))'),{'pid':pid}).scalar_one():
                        assert not answer.done()
                        assert time.monotonic()<deadline
                        time.sleep(.01)
                transaction.commit()
                with pytest.raises(KnowledgeIndexChanged):
                    answer.result(timeout=5)
            finally:
                if transaction.is_active:
                    transaction.rollback()


def test_answer_identity_locks_hold_revocation_until_commit(staged_plan,monkeypatch):
    bundle,controller,_,start,tokens=staged_plan
    controller.start(**start)
    owner,sid,rid=claimed(bundle,tokens[0])
    owner.pin_rag_route(run_id=rid,session_id=sid)
    reviewer=bundle.identity.resolve(tokens[1])
    reached,proceed=Event(),Event()
    original=owner._insert_message
    def pause(db,**values):
        if values['role']=='assistant':
            reached.set()
            assert proceed.wait(5)
        return original(db,**values)
    monkeypatch.setattr(owner,'_insert_message',pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answer=pool.submit(complete,owner,sid,rid)
        assert reached.wait(5)
        revocation=pool.submit(bundle.identity.update_membership,reviewer.tenant_id,reviewer.user_id,
            role='customer',active=True)
        try:
            deadline=time.monotonic()+5
            with bundle.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                while not observer.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type='Lock' AND query LIKE 'UPDATE identity_memberships%')")).scalar_one():
                    assert not revocation.done()
                    assert time.monotonic()<deadline
                    time.sleep(.01)
            proceed.set()
            assert answer.result(timeout=5)
            revocation.result(timeout=5)
        finally:
            proceed.set()
