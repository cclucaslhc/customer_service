"""Synthetic RAG release controls inside the real immutable runtime image."""
import subprocess
import sys
import uuid
import sqlalchemy as sa
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.schema import metadata
from app.rag_index_admin import RagIndexAdmin


# The preceding image_knowledge_acceptance.py migrates this owned disposable DB.
url='postgresql+psycopg://integration:local-integration-only@127.0.0.1:5432/postgres'
bundle=PostgreSQLRepositories.from_url(url,pool_size=2)
try:
    bundle.check_schema()
    tenant=bundle.identity.create_tenant('Synthetic immutable RAG release')
    admin=RagIndexAdmin(bundle.engine)
    indexes=metadata.tables['knowledge_index_versions']
    def staged(profile=None):
        index=uuid.uuid4()
        if profile is None:
            profile=uuid.uuid4()
            admin.stage(tenant_id=tenant,profile_id=profile,index_id=index,max_tokens=8192,
                lexical_version='synthetic',chunker_version='synthetic')
        else:
            admin.stage_existing(tenant_id=tenant,profile_id=profile,index_id=index,
                lexical_version='synthetic',chunker_version='synthetic')
        assert admin.seal(tenant_id=tenant,index_id=index)['current_chunks']==0
        return profile,index
    first_profile,first=staged()
    try:
        admin.activate(tenant_id=tenant,index_id=first)
    except ValueError as exc:
        assert 'Explicit expected' in str(exc)
    else:
        raise AssertionError('Missing CAS precondition was accepted')
    admin.activate(tenant_id=tenant,index_id=first,expected_active_index_id=None)
    _,second=staged()
    original_audit=admin._audit
    def unavailable(*args,**kwargs):
        raise RuntimeError('Synthetic runtime audit outage')
    admin._audit=unavailable
    try:
        try:
            admin.activate(tenant_id=tenant,index_id=second,expected_active_index_id=first)
        except RuntimeError as exc:
            assert 'audit outage' in str(exc)
        else:
            raise AssertionError('Audit outage did not prevent publication')
    finally:
        admin._audit=original_audit
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.tenant_id==uuid.UUID(tenant),
            indexes.c.status=='active')).scalar_one()==first
    def activate_cli(index,expected):
        result=subprocess.run([sys.executable,'scripts/rag_index_admin.py','--database-url',url,
            '--tenant-id',tenant,'--index-id',str(index),'activate',
            '--expected-active-index-id',str(expected)],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
    activate_cli(second,first)
    _,rollback=staged(first_profile)
    activate_cli(rollback,second)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.index_id).where(indexes.c.tenant_id==uuid.UUID(tenant),
            indexes.c.status=='active')).scalar_one()==rollback
        audit=metadata.tables['audit_logs']
        rows=db.execute(sa.select(audit).where(audit.c.action.like('rag.index.%'))).mappings().all()
        rows=[row for row in rows if row['metadata_json'].get('target_tenant_id')==tenant]
        assert len(rows)==9
        activations=[row for row in rows if row['action']=='rag.index.activate']
        assert len(activations)==3
        assert {row['metadata_json']['previous_index_id'] for row in activations}=={None,str(first),str(second)}
    print('immutable image RAG audit/CAS, audit-failure rollback and CLI forward/rollback passed')
finally:
    bundle.close()
