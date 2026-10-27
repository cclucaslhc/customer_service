"""Real PG least-privilege projection, not actual retrieval/answer routing."""
import hashlib
import uuid
import pytest
import sqlalchemy as sa
from app.identity import token_hash
from app.persistence.durable_chat_repository import DurableChatRepository
from app.persistence.schema import metadata
from test_durable_chat import submit
from test_rag_rollout_service import staged_plan,planned,release,queue_setup,postgres_database


SQL=sa.text('SELECT * FROM public.cs_rag_route(:session,:hash)')


def session(bundle,token):
    return submit(DurableChatRepository(bundle.engine).authenticated(token))[0]


def query(bundle,sid,token):
    with bundle.engine.connect() as db:
        return db.execute(SQL,{'session':uuid.UUID(sid),'hash':token_hash(token)}).mappings().all()


def test_projection_is_session_scoped_deterministic_and_pause_invalidates(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    sid=session(bundle,tokens[0])
    assert query(bundle,sid,tokens[0])==[]
    started=controller.start(**start)
    rows=query(bundle,sid,tokens[0])
    assert len(rows)==1 and rows==query(bundle,sid,tokens[0])
    assert set(rows[0])=={'tenant_id','rollout_id','release_id','route_version','index_id'}
    with bundle.engine.connect() as db:
        r=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        i=db.execute(sa.select(metadata.tables['rag_release_intents']).where(
            metadata.tables['rag_release_intents'].c.release_id==r['release_id'])).mappings().one()
    encoded=f"{r['tenant_id']}:{sid}:{r['rollout_id']}:{r['plan']['cohort_salt']}"
    bucket=int(hashlib.md5(encoded.encode()).hexdigest()[:8],16)%10000
    assert rows[0]['index_id']==(i['candidate_index_id'] if bucket<100 else i['previous_index_id'])
    control['expected_rollout_version']=started['version']
    controller.pause(**control)
    assert query(bundle,sid,tokens[0])==[]


def test_projection_refuses_foreign_customer_and_revoked_approver(staged_plan):
    bundle,controller,_,start,tokens=staged_plan
    sid=session(bundle,tokens[0])
    controller.start(**start)
    tenant=start['tenant_id']
    bundle.identity.provision_user('synthetic.route.other','synthetic-password-only',tenant,role='customer')
    other=bundle.identity.login('synthetic.route.other','synthetic-password-only',tenant).token
    assert query(bundle,sid,other)==[]
    foreign=bundle.identity.create_tenant('Synthetic foreign routing')
    bundle.identity.provision_user('synthetic.route.foreign','synthetic-password-only',foreign,role='admin')
    foreign_token=bundle.identity.login('synthetic.route.foreign','synthetic-password-only',foreign).token
    assert query(bundle,sid,foreign_token)==[]
    assert query(bundle,sid,tokens[0])
    bundle.identity.logout(tokens[1])
    assert query(bundle,sid,tokens[0])==[]


def test_projection_execute_grant_does_not_expose_control_tables(staged_plan):
    bundle,controller,_,start,tokens=staged_plan
    sid=session(bundle,tokens[0])
    controller.start(**start)
    role='cs_route_probe_'+uuid.uuid4().hex
    with bundle.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOINHERIT')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    try:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert not db.execute(sa.text("SELECT has_function_privilege(current_user,"
                "'public.cs_rag_route_lock(uuid,text)','EXECUTE')")).scalar_one()
            assert not db.execute(sa.text("SELECT has_function_privilege(current_user,"
                "'public.cs_rag_route_context(uuid,text,text,text)','EXECUTE')")).scalar_one()
            with pytest.raises(sa.exc.DBAPIError) as failure,db.begin_nested():
                db.execute(SQL,{'session':uuid.UUID(sid),'hash':token_hash(tokens[0])})
            assert failure.value.orig.sqlstate=='42501'
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.cs_rag_route(uuid,text) TO "{role}"')
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.cs_rag_route_lock(uuid,text) TO "{role}"')
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.cs_rag_route_context(uuid,text,text,text) TO "{role}"')
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            db.exec_driver_sql('SET LOCAL search_path=pg_temp,public')
            assert len(db.execute(SQL,{'session':uuid.UUID(sid),'hash':token_hash(tokens[0])}).all())==1
            assert len(db.execute(sa.text('SELECT * FROM public.cs_rag_route_lock(:session,:hash)'),
                {'session':uuid.UUID(sid),'hash':token_hash(tokens[0])}).all())==1
            predicate=sa.text('SELECT public.cs_rag_route_context(:session,:hash,:model,:prompt)')
            bound={'session':uuid.UUID(sid),'hash':token_hash(tokens[0]),'model':'a'*64,'prompt':'a'*64}
            assert db.execute(predicate,bound).scalar_one()
            assert not db.execute(predicate,{**bound,'model':'b'*64}).scalar_one()
            assert not db.execute(SQL,{'session':uuid.UUID(sid),'hash':'0'*64}).all()
            for name in ('rag_release_intents','rag_rollouts','identity_sessions'):
                with pytest.raises(sa.exc.DBAPIError) as failure,db.begin_nested():
                    db.exec_driver_sql(f'SELECT * FROM public.{name}')
                assert failure.value.orig.sqlstate=='42501'
    finally:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY "{role}"')
            db.exec_driver_sql(f'DROP ROLE "{role}"')


def test_projection_exercises_both_candidate_and_baseline_cohorts(staged_plan):
    bundle,controller,control,start,tokens=staged_plan
    controller.start(**start)
    selected={}
    for number in range(1,100001):
        sid=str(uuid.UUID(int=number))
        encoded=f"{start['tenant_id']}:{sid}:{control['rollout_id']}:11111111-1111-4111-8111-111111111111"
        candidate=int(hashlib.md5(encoded.encode()).hexdigest()[:8],16)%10000<100
        selected.setdefault(candidate,sid)
        if len(selected)==2:
            break
    assert len(selected)==2
    owner=DurableChatRepository(bundle.engine).authenticated(tokens[0])
    for candidate,sid in selected.items():
        submit(owner,sid=sid)
        result=query(bundle,sid,tokens[0])
        assert len(result)==1
        assert (str(result[0]['index_id'])==start['current_binding']['index_id']) is candidate
