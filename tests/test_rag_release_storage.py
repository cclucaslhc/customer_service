"""Real PG schema boundary only; no approval service or release is authorized."""
from datetime import datetime, timedelta, timezone
import uuid
import pytest
import sqlalchemy as sa
from app.backup import invalidate_restored_release_approvals
from app.rag_index_admin import RagIndexAdmin
from app.persistence.schema import metadata
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database


def record(bundle,tokens):
    principal=bundle.identity.resolve(tokens['synthetic.admin'])
    tenant=uuid.UUID(principal.tenant_id)
    second=bundle.identity.provision_user('synthetic.storage.reviewer','synthetic-password-only',
        str(tenant),role='admin')
    admin=RagIndexAdmin(bundle.engine)
    indexes=[]
    for _ in range(2):
        index=uuid.uuid4()
        admin.stage(tenant_id=tenant,profile_id=uuid.uuid4(),index_id=index,max_tokens=8192,
            lexical_version='synthetic',chunker_version='synthetic')
        indexes.append(index)
    now=datetime.now(timezone.utc)
    return dict(tenant_id=tenant,release_id=uuid.uuid4(),previous_index_id=indexes[0],
        candidate_index_id=indexes[1],reviewer_a=uuid.UUID(principal.user_id),reviewer_b=uuid.UUID(second),
        body={'scope':'synthetic_storage_contract_not_approval'},body_sha256='a'*64,
        created_at=now,expires_at=now+timedelta(minutes=30))


def test_release_storage_rejects_cross_tenant_duplicate_reviewers_and_definition_rewrite(queue_setup):
    bundle,_,_,tokens=queue_setup
    row=record(bundle,tokens)
    table=metadata.tables['rag_release_intents']
    for changes in ({'reviewer_b':row['reviewer_a']},{'candidate_index_id':row['previous_index_id']},
                    {'state':'approved'},{'expires_at':row['created_at']},
                    {'state':'approved','approval_a_session_hash':'b'*64,'approval_b_session_hash':'b'*64,
                     'approval_a_at':row['created_at'],'approval_b_at':row['created_at']}):
        with pytest.raises(sa.exc.IntegrityError),bundle.engine.begin() as db:
            db.execute(table.insert().values(**{**row,**changes}))
    other=bundle.identity.create_tenant('Synthetic foreign release reviewer')
    foreign=bundle.identity.provision_user('synthetic.storage.foreign','synthetic-password-only',other,role='admin')
    with pytest.raises(sa.exc.IntegrityError),bundle.engine.begin() as db:
        db.execute(table.insert().values(**{**row,'reviewer_b':uuid.UUID(foreign)}))
    with bundle.engine.begin() as db:
        db.execute(table.insert().values(**row))
    for changes in ({'body':{'changed':True}},{'body_sha256':'b'*64},
                    {'reviewer_a':row['reviewer_b']},{'expires_at':row['expires_at']+timedelta(days=1)}):
        with pytest.raises(sa.exc.DBAPIError,match='definition is immutable'),bundle.engine.begin() as db:
            db.execute(table.update().values(**changes))
    with bundle.engine.connect() as db:
        stored=db.execute(sa.select(table)).mappings().one()
        assert stored['state']=='pending' and stored['body']==row['body']
    with pytest.raises(sa.exc.DBAPIError,match='revision must advance'),bundle.engine.begin() as db:
        db.execute(table.update().values(state='rejected'))
    with bundle.engine.begin() as db:
        assert db.execute(table.update().where(table.c.version==0).values(state='rejected',version=1)).rowcount==1
        assert db.execute(table.update().where(table.c.version==0).values(state='rejected',version=1)).rowcount==0


def test_restore_requires_review_without_erasing_approval_evidence(queue_setup):
    bundle,_,_,tokens=queue_setup
    row=record(bundle,tokens)
    table=metadata.tables['rag_release_intents']
    states=('pending','approved','executed','rejected')
    with bundle.engine.begin() as db:
        for state in states:
            db.execute(table.insert().values(**{**row,'release_id':uuid.uuid4(),'state':state,
                'approval_a_session_hash':'b'*64,'approval_b_session_hash':'c'*64,
                'approval_a_at':row['created_at'],'approval_b_at':row['created_at']}))
        assert invalidate_restored_release_approvals(db)==2
        assert invalidate_restored_release_approvals(db)==0
    with bundle.engine.connect() as db:
        restored=db.execute(sa.select(table)).mappings().all()
    assert sorted(item['state'] for item in restored)==['executed','recovery_review','recovery_review','rejected']
    assert all(item['body']==row['body'] and item['approval_a_session_hash']=='b'*64 for item in restored)
    assert all(item['version']==(1 if item['state']=='recovery_review' else 0) for item in restored)


def test_unprivileged_role_cannot_read_or_forge_release_state(queue_setup):
    bundle,_,_,tokens=queue_setup
    role='cs_release_probe_'+uuid.uuid4().hex
    with bundle.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOINHERIT')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    try:
        for sql in ('SELECT * FROM rag_release_intents',
                    'INSERT INTO rag_release_intents DEFAULT VALUES',
                    'UPDATE rag_release_intents SET version=version WHERE false',
                    'DELETE FROM rag_release_intents WHERE false'):
            with bundle.engine.begin() as db:
                db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                with pytest.raises(sa.exc.DBAPIError) as failure,db.begin_nested():
                    db.exec_driver_sql(sql)
                assert failure.value.orig.sqlstate=='42501'
    finally:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY "{role}"')
            db.exec_driver_sql(f'DROP ROLE "{role}"')
