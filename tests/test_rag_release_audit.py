"""Real PG atomic owner audit and mandatory CAS for index publication."""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
import sqlalchemy as sa
from app.rag_index_admin import RagIndexAdmin
from app.persistence.schema import metadata
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database


def staged(admin,tenant):
    profile,index=uuid.uuid4(),uuid.uuid4()
    admin.stage(tenant_id=tenant,profile_id=profile,index_id=index,max_tokens=8192,
        lexical_version='synthetic',chunker_version='synthetic')
    return profile,index


def test_rag_release_audit_records_activation_and_rollback_without_content(queue_setup):
    bundle,_,_,tokens=queue_setup
    tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    admin=RagIndexAdmin(bundle.engine)
    first_profile,first=staged(admin,tenant)
    admin.seal(tenant_id=tenant,index_id=first)
    with pytest.raises(ValueError,match='Explicit expected'):
        admin.activate(tenant_id=tenant,index_id=first)
    admin.activate(tenant_id=tenant,index_id=first,expected_active_index_id=None)
    _,second=staged(admin,tenant)
    admin.seal(tenant_id=tenant,index_id=second)
    with pytest.raises(ValueError,match='changed since'):
        admin.activate(tenant_id=tenant,index_id=second,expected_active_index_id=None)
    admin.activate(tenant_id=tenant,index_id=second,expected_active_index_id=first)
    rollback=uuid.uuid4()
    admin.stage_existing(tenant_id=tenant,profile_id=first_profile,index_id=rollback,
        lexical_version='synthetic',chunker_version='synthetic')
    admin.seal(tenant_id=tenant,index_id=rollback)
    admin.activate(tenant_id=tenant,index_id=rollback,expected_active_index_id=second)
    with bundle.engine.connect() as db:
        audit=metadata.tables['audit_logs']
        rows=db.execute(sa.select(audit).where(audit.c.action.like('rag.index.%'))
            .order_by(audit.c.created_at)).mappings().all()
        role=db.execute(sa.text('SELECT current_user')).scalar_one()
    assert len(rows)==9
    assert all(str(row['tenant_id'])==LEGACY_TENANT_ID and row['metadata_json']['target_tenant_id']==tenant
        and row['actor_id'] is None and row['actor']==role for row in rows)
    activated=[row for row in rows if row['action']=='rag.index.activate']
    assert [row['metadata_json']['previous_index_id'] for row in activated]==[None,str(first),str(second)]
    assert [row['entity_id'] for row in activated]==list(map(str,(first,second,rollback)))
    assert all(set(row['metadata_json'])=={'target_tenant_id','previous_index_id','profile_id','manifest_sha256','current_chunks'}
        for row in activated)
    assert 'synthetic-password-only' not in repr(rows)


def test_rag_audit_failure_rolls_back_stage_seal_and_active_switch(queue_setup,monkeypatch):
    bundle,_,_,tokens=queue_setup
    tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    admin=RagIndexAdmin(bundle.engine)
    first_profile,first=staged(admin,tenant)
    admin.seal(tenant_id=tenant,index_id=first)
    admin.activate(tenant_id=tenant,index_id=first,expected_active_index_id=None)
    _,second=staged(admin,tenant)
    def unavailable(*args,**kwargs):
        raise RuntimeError('Synthetic audit unavailable')
    indexes=metadata.tables['knowledge_index_versions']
    profiles=metadata.tables['embedding_profiles']
    with monkeypatch.context() as patch:
        patch.setattr(admin,'_audit',unavailable)
        with pytest.raises(RuntimeError,match='audit unavailable'):
            staged(admin,tenant)
        with pytest.raises(RuntimeError,match='audit unavailable'):
            admin.stage_existing(tenant_id=tenant,profile_id=first_profile,index_id=uuid.uuid4(),
                lexical_version='synthetic',chunker_version='synthetic')
        with pytest.raises(RuntimeError,match='audit unavailable'):
            admin.seal(tenant_id=tenant,index_id=second)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(indexes)).scalar_one()==2
        assert db.execute(sa.select(sa.func.count()).select_from(profiles)).scalar_one()==2
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id==second)).scalar_one()=='building'
    admin.seal(tenant_id=tenant,index_id=second)
    with monkeypatch.context() as patch:
        patch.setattr(admin,'_audit',unavailable)
        with pytest.raises(RuntimeError,match='audit unavailable'):
            admin.activate(tenant_id=tenant,index_id=second,expected_active_index_id=first)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.status=='active')).scalar_one()==first
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id==second)).scalar_one()=='ready'


def test_concurrent_rag_activation_has_one_cas_winner_and_one_atomic_audit(queue_setup):
    bundle,_,_,tokens=queue_setup
    tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    admin=RagIndexAdmin(bundle.engine)
    _,first=staged(admin,tenant)
    admin.seal(tenant_id=tenant,index_id=first)
    admin.activate(tenant_id=tenant,index_id=first,expected_active_index_id=None)
    candidates=[]
    for _ in range(2):
        _,index=staged(admin,tenant)
        admin.seal(tenant_id=tenant,index_id=index)
        candidates.append(index)
    def activate(index):
        try:
            admin.activate(tenant_id=tenant,index_id=index,expected_active_index_id=first)
            return index
        except ValueError as exc:
            assert 'changed since rollout approval' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners=[value for value in pool.map(activate,candidates) if value]
    assert len(winners)==1
    with bundle.engine.connect() as db:
        indexes=metadata.tables['knowledge_index_versions']
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.status=='active')).scalar_one()==winners[0]
        audit=metadata.tables['audit_logs']
        activated=db.execute(sa.select(audit).where(audit.c.action=='rag.index.activate')).mappings().all()
        assert len(activated)==2
        assert sum(row['metadata_json']['previous_index_id']==str(first) for row in activated)==1
