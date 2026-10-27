import uuid
import pytest
import sqlalchemy as sa
from app.persistence.schema import metadata
from test_rag_rollout_advance import (
    advancing,short_plan,seed_run,planned,staged_plan,release,queue_setup,postgres_database,
)

pytestmark=pytest.mark.parametrize('planned',[{'rollout_plan':short_plan()}],indirect=True)


def published(advancing):
    bundle,controller,args,tokens=advancing
    for _ in range(2):
        seed_run(bundle,args,tokens)
        result=controller.advance(**args)
        args.update(expected_rollout_version=result['version'],expected_release_version=result['release_version'])
    receipt={key:value for key,value in args.items() if key!='current_binding'}
    return bundle,controller,receipt


def test_preparation_is_new_identity_not_activation_or_approval(advancing):
    bundle,controller,receipt=published(advancing)
    fresh=uuid.uuid4()
    result=controller.prepare_reversion(**receipt,new_index_id=fresh)
    assert result['scope']=='ready_reversion_candidate_requires_new_quality_and_approvals'
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        rows=db.execute(sa.select(indexes)).mappings().all()
        assert next(row for row in rows if row['index_id']==fresh)['status']=='ready'
        assert str(next(row for row in rows if row['status']=='active')['index_id'])==result['expected_active_index_id']
        assert sum(row['status']=='retired' for row in rows)==1
        assert db.execute(sa.select(metadata.tables['rag_rollouts'].c.state)).scalar_one()=='completed'


def test_preparation_audit_failure_rolls_back_fresh_candidate(advancing,monkeypatch):
    bundle,controller,receipt=published(advancing)
    fresh=uuid.uuid4()
    def fail(*a,**kw):
        raise RuntimeError('synthetic audit outage')
    monkeypatch.setattr(controller.releases.admin,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit outage'):
        controller.prepare_reversion(**receipt,new_index_id=fresh)
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes).where(indexes.c.index_id==fresh)).first() is None


def test_running_rollout_cannot_prepare_postpublication_reversion(advancing):
    bundle,controller,args,tokens=advancing
    with pytest.raises(ValueError,match='completed rollout'):
        controller.prepare_reversion(**{key:value for key,value in args.items() if key!='current_binding'},
            new_index_id=uuid.uuid4())


def test_changed_manifest_cannot_resurrect_stale_document_snapshot(advancing,monkeypatch):
    bundle,controller,receipt=published(advancing)
    fresh=uuid.uuid4()
    monkeypatch.setattr(controller.releases.admin,'_manifest',lambda *args:('f'*64,0))
    with pytest.raises(ValueError,match='no longer covers current documents'):
        controller.prepare_reversion(**receipt,new_index_id=fresh)
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes).where(indexes.c.index_id==fresh)).first() is None


def test_fresh_approved_reversion_rejects_inflight_published_answer(advancing):
    from datetime import datetime,timezone
    from copy import deepcopy
    from app.persistence.durable_chat_repository import DurableChatRepository
    from test_durable_chat import submit
    from test_run_rag_route import complete
    from test_rag_release_service import approve_both
    bundle,controller,receipt=published(advancing)
    tokens=advancing[3]
    root=DurableChatRepository(bundle.engine)
    sid,rid=submit(root.authenticated(tokens[0]))
    job=root.claim_next()
    owner=root.authenticated(job['credential']).with_lease(job['lease'])
    pinned=owner.pin_rag_route(run_id=rid,session_id=sid)
    fresh=uuid.uuid4()
    prepared=controller.prepare_reversion(**receipt,new_index_id=fresh)
    with bundle.engine.connect() as db:
        releases=metadata.tables['rag_release_intents']
        definition=deepcopy(db.execute(sa.select(releases.c.body).where(
            releases.c.state=='executed')).scalar_one())
    binding={**definition['binding'],'index_id':str(fresh),'manifest_sha256':prepared['manifest_sha256']}
    evidence={**definition['evidence'],**binding,'completed_at':datetime.now(timezone.utc).isoformat()}
    service=controller.releases
    new=service.create(release_id=uuid.uuid4(),previous_index_id=prepared['expected_active_index_id'],
        policy=definition['policy'],evidence=evidence,binding=binding,lifetime_seconds=600)
    args=dict(tenant_id=receipt['tenant_id'],release_id=new['release_id'],expected_version=0,
        expected_body_sha256=new['body_sha256'],current_binding=binding)
    approve_both(service,args,tokens)
    assert service.execute(**args)['state']=='executed'
    assert pinned['index_id']!=str(fresh)
    from app.knowledge_retrieval import KnowledgeIndexChanged
    with pytest.raises(KnowledgeIndexChanged):
        complete(owner,sid,rid)
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.status=='active')).scalar_one()==fresh
