"""Actual PG transition transactions; synthetic telemetry is not quality acceptance."""
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
import pytest
import sqlalchemy as sa
from app.identity import AuthorizationError
from app.persistence.schema import metadata
from app.persistence.durable_chat_repository import DurableChatRepository
from test_durable_chat import submit
from test_run_rag_route import complete
from test_rag_routed_retrieval import candidate_session
from test_rag_rollout_plan import plan
from test_rag_rollout_service import staged_plan,planned,release,queue_setup,postgres_database


def short_plan():
    return {**plan(),'steps_bps':[100,500,10000],'min_requests':1,'min_observation_seconds':1}


pytestmark=pytest.mark.parametrize('planned',[{'rollout_plan':short_plan()}],indirect=True)


@pytest.fixture
def advancing(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    running=controller.start(**start)
    args={**start,'expected_rollout_version':running['version']}
    return bundle,controller,args,tokens


def seed_run(bundle,args,tokens,*,pending=False):
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(args))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    owner.pin_rag_route(run_id=rid,session_id=sid,runtime_context={
        'model_config_sha256':'a'*64,'prompt_sha256':'a'*64})
    if pending:
        return
    tenant=uuid.UUID(args['tenant_id'])
    attempt=uuid.uuid4()
    with bundle.engine.begin() as db:
        now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        db.execute(metadata.tables['model_usage_attempts'].insert().values(attempt_id=attempt,
            tenant_id=tenant,run_id=uuid.UUID(rid),actor_id=uuid.UUID(bundle.identity.resolve(tokens[0]).user_id),
            lease_owner=uuid.UUID(job['lease'].owner),lease_fence=job['lease'].fence,provider='deepseek',
            model='synthetic',purpose='chat',state='completed',usage_status='observed',
            input_tokens=1,output_tokens=1,cache_read_tokens=0,cache_write_tokens=0,started_at=now,finished_at=now))
        db.execute(metadata.tables['model_cost_assessments'].insert().values(assessment_id=uuid.uuid4(),
            tenant_id=tenant,attempt_id=attempt,revision=0,kind='estimate',status='estimated',
            reason='sdk_observed',amount='0.01',currency='CNY',created_at=now))
    assert complete(owner,sid,rid)
    # Real clock wait, no backdating the protected phase timestamp.
    time.sleep(1.02)


def observation_args(args):
    return {key:args[key] for key in ('tenant_id','rollout_id','expected_rollout_version','expected_plan_sha256')}


def test_steps_recollect_each_epoch_and_final_promotion_consumes_approval(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    first=controller.advance(**args)
    assert first['state']=='running' and first['basis_points']==500 and first['version']==2
    args['expected_rollout_version']=first['version']
    assert controller.observe(**observation_args(args))['report']['requests']==0
    with pytest.raises(ValueError,match='insufficient_requests'):
        controller.advance(**args)
    seed_run(bundle,args,tokens)
    final=controller.advance(**args)
    assert final['state']=='completed' and final['basis_points']==10000 and final['release_version']==3
    args['expected_rollout_version']=final['version']
    args['expected_release_version']=final['release_version']
    with pytest.raises(ValueError,match='consistent running'):
        controller.advance(**args)
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]),sid=candidate_session(args))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    published=owner.pin_rag_route(run_id=rid,session_id=sid)
    assert published['rollout_id'] is None and published['index_id']==args['current_binding']['index_id']
    assert complete(owner,sid,rid)
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert str(db.execute(sa.select(indexes.c.index_id).where(indexes.c.status=='active')).scalar_one())==args['current_binding']['index_id']
        releases=metadata.tables['rag_release_intents']
        rollouts=metadata.tables['rag_rollouts']
        release_id=db.execute(sa.select(rollouts.c.release_id).where(
            rollouts.c.rollout_id==uuid.UUID(args['rollout_id']))).scalar_one()
        assert db.execute(sa.select(releases.c.state).where(releases.c.release_id==release_id)).scalar_one()=='executed'
        audit=metadata.tables['audit_logs']
        records=db.execute(sa.select(audit.c.metadata_json).where(audit.c.action.in_(
            ('rag.index.rollout_advance','rag.index.rollout_complete')))).scalars().all()
        assert len(records)==2 and all(row['observation']['assessment']['thresholds_satisfied'] for row in records)


def test_final_audit_failure_rolls_back_index_approval_and_phase(advancing,monkeypatch):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    args['expected_rollout_version']=controller.advance(**args)['version']
    seed_run(bundle,args,tokens)
    original=controller.releases.admin._audit
    def fail(db,tenant,index,action,details):
        if action=='rollout_complete':
            raise RuntimeError('Synthetic final rollout audit outage')
        return original(db,tenant,index,action,details)
    monkeypatch.setattr(controller.releases.admin,'_audit',fail)
    with pytest.raises(RuntimeError,match='rollout audit'):
        controller.advance(**args)
    with bundle.engine.connect() as db:
        rollouts,releases,indexes=(metadata.tables[name] for name in
            ('rag_rollouts','rag_release_intents','knowledge_index_versions'))
        state=db.execute(sa.select(rollouts).where(rollouts.c.rollout_id==uuid.UUID(args['rollout_id']))).mappings().one()
        assert state['state']=='running' and state['version']==2 and state['basis_points']==500
        retained=db.execute(sa.select(releases).where(releases.c.release_id==state['release_id'])).mappings().one()
        assert retained['state']=='approved'
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.status=='active')).scalar_one()==retained['previous_index_id']
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id==uuid.UUID(args['current_binding']['index_id']))).scalar_one()=='ready'


def test_saved_green_observation_cannot_override_new_pending_request(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    assert controller.observe(**observation_args(args))['assessment']['thresholds_satisfied']
    seed_run(bundle,args,tokens,pending=True)
    with pytest.raises(ValueError,match='pending_runs'):
        controller.advance(**args)
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        assert row['version']==1 and row['basis_points']==100


def test_reviewer_revocation_blocks_otherwise_healthy_transition(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    bundle.identity.logout(tokens[1])
    with pytest.raises(AuthorizationError):
        controller.advance(**args)


def test_same_version_concurrent_advance_has_one_winner(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    def advance(_):
        try:
            return controller.advance(**args)
        except ValueError as exc:
            assert 'receipt changed' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(advance,range(2)))
    assert sum(row is not None for row in results)==1


def test_control_cli_advances_then_rejects_stale_receipt_and_pauses(advancing,tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    path=tmp_path/'control.json'
    path.write_text(json.dumps(args),encoding='utf-8')
    if os.name=='posix': path.chmod(0o600)
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/rag_rollout_control.py'),
        '--database-url',bundle.engine.url.render_as_string(hide_password=False),
        '--control-file',str(path)]
    refused=subprocess.run([*command,'advance'],capture_output=True,text=True,timeout=30)
    assert refused.returncode==2 and 'Mutation requires' in refused.stderr
    first=subprocess.run([*command,'--confirm-change','advance'],capture_output=True,text=True,timeout=30)
    assert first.returncode==0,first.stderr
    receipt=json.loads(first.stdout)
    assert receipt['basis_points']==500
    stale=subprocess.run([*command,'--confirm-change','pause'],capture_output=True,text=True,timeout=30)
    assert stale.returncode==1 and stale.stdout==''
    args['expected_rollout_version']=receipt['version']
    path.write_text(json.dumps(args),encoding='utf-8')
    stopped=subprocess.run([*command,'--confirm-change','pause'],capture_output=True,text=True,timeout=30)
    assert stopped.returncode==0,stopped.stderr
    assert json.loads(stopped.stdout)['basis_points']==0
