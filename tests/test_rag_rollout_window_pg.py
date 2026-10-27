"""Real PG-derived windows, synthetic runs; no external model or invoice proof."""
import uuid
import hashlib
import pytest
import sqlalchemy as sa
from app.persistence.schema import metadata
from app.persistence.durable_chat_repository import DurableChatRepository
from test_durable_chat import submit
from test_run_rag_route import complete
from test_rag_routed_retrieval import candidate_session
from test_rag_rollout_service import staged_plan,planned,release,queue_setup,postgres_database
from test_rag_rollout_plan import plan


def test_window_reads_current_candidate_runs_and_never_treats_missing_fees_as_zero(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    started=controller.start(**start)
    control['expected_rollout_version']=started['version']
    empty=controller.observe(**control)
    assert empty['report']['requests']==0 and not empty['assessment']['thresholds_satisfied']
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(control))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    route=owner.pin_rag_route(run_id=rid,session_id=sid,runtime_context={
        'model_config_sha256':'a'*64,'prompt_sha256':'a'*64})
    pending=controller.observe(**control)
    assert pending['report']['requests']==pending['report']['pending']==1
    assert 'pending_runs' in pending['assessment']['reason_codes']
    assert complete(owner,sid,rid)
    result=controller.observe(**control)
    assert result['report']['completed']==1 and result['report']['pending']==0
    assert result['report']['unknown_cost_runs']==1 and result['report']['max_cost_per_request'] is None
    assert 'unknown_cost' in result['assessment']['reason_codes']
    assert result['report']['invalid_context_runs']==0
    table=metadata.tables['rag_rollouts']
    with bundle.engine.begin() as db:
        # Owner simulates the next phase, using DB time. Old epoch samples must
        # not satisfy the new phase's minimum. This is not an advance API.
        db.execute(table.update().where(table.c.rollout_id==uuid.UUID(control['rollout_id'])).values(
            step_index=1,basis_points=500,version=2,step_started_at=sa.func.clock_timestamp()))
    control['expected_rollout_version']=2
    assert controller.observe(**control)['report']['requests']==0


def test_phase_clock_cannot_be_backdated_or_reset_within_same_step(staged_plan):
    bundle,controller,control,start,_=staged_plan
    result=controller.start(**start)
    table=metadata.tables['rag_rollouts']
    for changed in ({'step_started_at':sa.func.clock_timestamp()},
                    {'step_index':1,'basis_points':500}):
        with pytest.raises(sa.exc.DBAPIError),bundle.engine.begin() as db:
            db.execute(table.update().where(table.c.rollout_id==uuid.UUID(control['rollout_id'])).values(
                version=result['version']+1,**changed))
    with bundle.engine.connect() as db:
        stored=db.execute(sa.select(table)).mappings().one()
        assert stored['version']==result['version'] and stored['step_started_at'] is not None


def test_baseline_requests_never_satisfy_candidate_window(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    running=controller.start(**start)
    control['expected_rollout_version']=running['version']
    for number in range(1,100001):
        sid=str(uuid.UUID(int=number))
        encoded=f"{control['tenant_id']}:{sid}:{control['rollout_id']}:{plan()['cohort_salt']}"
        if int(hashlib.md5(encoded.encode()).hexdigest()[:8],16)%10000>=100:
            break
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=sid)
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    route=owner.pin_rag_route(run_id=rid,session_id=sid,runtime_context={
        'model_config_sha256':'a'*64,'prompt_sha256':'a'*64})
    assert route['index_id']!=start['current_binding']['index_id']
    assert complete(owner,sid,rid)
    assert controller.observe(**control)['report']['requests']==0


def test_missing_attempt_cost_is_not_ignored_and_known_attempt_costs_are_summed(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    running=controller.start(**start)
    control['expected_rollout_version']=running['version']
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(control))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    owner.pin_rag_route(run_id=rid,session_id=sid,runtime_context={
        'model_config_sha256':'a'*64,'prompt_sha256':'a'*64})
    attempts,costs=metadata.tables['model_usage_attempts'],metadata.tables['model_cost_assessments']
    ids=[uuid.uuid4(),uuid.uuid4()]
    tenant=uuid.UUID(control['tenant_id'])
    actor=uuid.UUID(bundle.identity.resolve(tokens[0]).user_id)
    with bundle.engine.begin() as db:
        now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        for identifier in ids:
            db.execute(attempts.insert().values(attempt_id=identifier,tenant_id=tenant,run_id=uuid.UUID(rid),
                actor_id=actor,lease_owner=uuid.UUID(job['lease'].owner),lease_fence=job['lease'].fence,
                provider='deepseek',model='synthetic',purpose='chat',state='completed',usage_status='observed',
                input_tokens=1,output_tokens=1,cache_read_tokens=0,cache_write_tokens=0,started_at=now,finished_at=now))
        db.execute(costs.insert().values(assessment_id=uuid.uuid4(),tenant_id=tenant,attempt_id=ids[0],
            revision=0,kind='estimate',status='estimated',reason='sdk_observed',amount='0.07',currency='CNY',created_at=now))
    assert complete(owner,sid,rid)
    missing=controller.observe(**control)
    assert missing['report']['unknown_cost_runs']==1 and missing['report']['max_cost_per_request'] is None
    with bundle.engine.begin() as db:
        db.execute(costs.insert().values(assessment_id=uuid.uuid4(),tenant_id=tenant,attempt_id=ids[1],
            revision=0,kind='estimate',status='estimated',reason='sdk_observed',amount='0.05',currency='CNY',
            created_at=sa.func.clock_timestamp()))
    known=controller.observe(**control)
    assert known['report']['unknown_cost_runs']==0
    assert known['report']['max_cost_per_request']=='0.120000000'
    assert 'cost_exceeded' in known['assessment']['reason_codes']
