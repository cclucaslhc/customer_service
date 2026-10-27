"""Approved-plan staging only on disposable PG; no traffic or real quality proof."""
import uuid
import pytest
import sqlalchemy as sa
from app.identity import AuthorizationError
from app.persistence.schema import metadata
from app.rag_rollout_service import RagRolloutService
from test_rag_release_service import release,queue_setup,postgres_database,approve_both
from test_rag_rollout_plan import plan


@pytest.fixture
def planned(release,request):
    bundle,service,args,tokens,*_=release
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['rag_release_intents'])).mappings().one()
    definition=row['body']
    receipt=service.create(release_id=uuid.uuid4(),previous_index_id=row['previous_index_id'],
        policy=definition['policy'],evidence=definition['evidence'],binding=definition['binding'],
        lifetime_seconds=600,rollout_plan=getattr(request,'param',{}).get('rollout_plan',plan()))
    args={**args,'release_id':receipt['release_id'],'expected_body_sha256':receipt['body_sha256']}
    return bundle,service,args,tokens


def test_only_approved_frozen_plan_can_be_staged_and_no_direct_execution(planned):
    bundle,service,args,tokens=planned
    controller=RagRolloutService(bundle.engine)
    with pytest.raises(AuthorizationError):
        controller.stage(rollout_id=uuid.uuid4(),**args)
    approve_both(service,args,tokens)
    with pytest.raises(ValueError,match='rollout controller'):
        service.execute(**args)
    result=controller.stage(rollout_id=uuid.uuid4(),**args)
    assert result['state']=='staged' and result['basis_points']==0
    with bundle.engine.connect() as db:
        stored=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        assert stored['plan']==plan() and stored['plan_sha256']==result['plan_sha256']
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id==uuid.UUID(args['current_binding']['index_id']))).scalar_one()=='ready'


def test_non_rollout_release_cannot_acquire_rollout_authority(release):
    bundle,service,args,tokens,*_=release
    approve_both(service,args,tokens)
    with pytest.raises(ValueError,match='no approved rollout plan'):
        RagRolloutService(bundle.engine).stage(rollout_id=uuid.uuid4(),**args)


def test_staging_rechecks_live_approvals_and_audit_rolls_back(planned,monkeypatch):
    bundle,service,args,tokens=planned
    approve_both(service,args,tokens)
    controller=RagRolloutService(bundle.engine)
    def fail(*a,**kw):
        raise RuntimeError('synthetic staging audit outage')
    with monkeypatch.context() as patch:
        patch.setattr(controller.releases.admin,'_audit',fail)
        with pytest.raises(RuntimeError,match='staging audit'):
            controller.stage(rollout_id=uuid.uuid4(),**args)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['rag_rollouts'])).scalar_one()==0
    bundle.identity.logout(tokens[1])
    with pytest.raises(AuthorizationError):
        controller.stage(rollout_id=uuid.uuid4(),**args)


@pytest.fixture
def staged_plan(planned):
    bundle,service,args,tokens=planned
    approve_both(service,args,tokens)
    controller=RagRolloutService(bundle.engine)
    receipt=controller.stage(rollout_id=uuid.uuid4(),**args)
    control=dict(tenant_id=args['tenant_id'],rollout_id=receipt['rollout_id'],
        expected_rollout_version=0,expected_plan_sha256=receipt['plan_sha256'])
    start={**control,'expected_release_version':args['expected_version'],
        'expected_body_sha256':args['expected_body_sha256'],'current_binding':args['current_binding']}
    return bundle,controller,control,start,tokens


def test_start_only_first_approved_step_then_pause_and_terminal_withdraw(staged_plan):
    bundle,controller,control,start,_=staged_plan
    result=controller.start(**start)
    assert result['state']=='running' and result['step_index']==0 and result['basis_points']==100
    with pytest.raises(ValueError,match='receipt changed'):
        controller.start(**start)
    control['expected_rollout_version']=result['version']
    paused=controller.pause(**control)
    assert paused['state']=='paused' and paused['basis_points']==0
    control['expected_rollout_version']=paused['version']
    result=controller.rollback(**control)
    assert result['state']=='rolled_back' and result['basis_points']==0
    control['expected_rollout_version']=result['version']
    with pytest.raises(ValueError,match='terminal'):
        controller.rollback(**control)
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id==uuid.UUID(start['current_binding']['index_id']))).scalar_one()=='ready'


def test_start_rechecks_approvals_but_owner_can_withdraw_after_revocation(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    bundle.identity.logout(tokens[1])
    with pytest.raises(AuthorizationError):
        controller.start(**start)
    assert controller.rollback(**control)['state']=='rolled_back'


def test_pause_still_available_after_reviewer_revocation(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    result=controller.start(**start)
    bundle.identity.logout(tokens[1])
    control['expected_rollout_version']=result['version']
    assert controller.pause(**control)['basis_points']==0


def test_pause_audit_outage_rolls_back_control_state(staged_plan,monkeypatch):
    bundle,controller,control,start,_=staged_plan
    result=controller.start(**start)
    control['expected_rollout_version']=result['version']
    def fail(*a,**kw):
        raise RuntimeError('synthetic pause audit failure')
    monkeypatch.setattr(controller.releases.admin,'_audit',fail)
    with pytest.raises(RuntimeError,match='pause audit'):
        controller.pause(**control)
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        assert row['state']=='running' and row['basis_points']==100 and row['version']==1
