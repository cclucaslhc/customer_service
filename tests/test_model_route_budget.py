"""Fallback-model contract uses one currency and the tenant's global cap."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.auth_http import AuthPolicy
from app.backup import create_backup, restore_backup, database_manifest
from app.database import utc_now
from app.identity import AuthorizationError
from app.main import create_app
from app.model_budget import ModelBudgetError, ModelBudgetExceeded
from app.persistence.model_cost_repository import CostConflict
from app.persistence.schema import metadata
from app.persistence.bundle import PostgreSQLRepositories
from app.run_leases import RunLease
from app.security import SecretStore
from test_model_budget import activate, request
from test_model_usage import prepare, queue_setup, postgres_database
from test_logical_backup import backup_setup, empty_database


def _prepare_route(queue_setup, *, global_limit='0.001'):
    bundle, root, owner, tokens, sid, rid, job, chat, primary_meter = prepare(queue_setup)
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    costs = admin.model_costs()
    for provider, model in (('deepseek','synthetic'), ('openai','synthetic-online')):
        costs.publish_price(publication_id=str(uuid4()),expected_version=0,
            provider=provider,model=model,currency='CNY',source_sha256='a'*64,
            input_rate='2',output_rate='8',cache_read_rate='1',cache_write_rate='3')
    budgets, global_policy, _ = activate(bundle,tokens,tenant_limit=global_limit,
        day_limit='1',run_limit='1')
    admin.update_provider('openai',model='synthetic-online',now=utc_now(),
        encrypted_key=b'synthetic-ciphertext',nonce=b'synthetic-nonce',replace_key=True)
    admin.model_fallbacks().publish(provider='openai',enabled=True,
        expected_version=0,publication_id=uuid4())
    alternate=chat.model_meter(rid,provider='openai',model='synthetic-online')
    return bundle,tokens,budgets,global_policy,primary_meter,alternate


def _publish(budgets, **changes):
    values=dict(publication_id=uuid4(),expected_version=0,provider='openai',
        model='synthetic-online',currency='CNY',input_token_ceiling=100,
        output_token_limit=10,source_sha256='b'*64)
    values.update(changes)
    return budgets.publish_route_policy(**values)


def test_fallback_route_contract_pins_version_and_shares_global_limit(queue_setup):
    bundle,tokens,budgets,global_policy,primary,alternate=_prepare_route(queue_setup)
    with pytest.raises(ModelBudgetError):
        alternate.begin(uuid4(),request=request())
    route=_publish(budgets)
    assert budgets.route_policy(provider='openai',model='synthetic-online')['version']==1
    first=uuid4(); primary.begin(first,request=request())
    second=uuid4(); alternate.begin(second,request=request())
    reservations=budgets.reservations(run_id=str(primary.run_id))
    assert len(reservations)==2
    assert {row['policy_id'] for row in reservations}=={global_policy['policy_id']}
    assert {row['route_policy_id'] for row in reservations}=={None,route['route_policy_id']}
    with pytest.raises(ModelBudgetExceeded):
        alternate.begin(uuid4(),request=request())
    assert len(primary.list_attempts())==2
    assert {row['provider'] for row in primary.list_attempts()}=={'deepseek','openai'}
    table=metadata.tables['model_route_budget_policies']
    with pytest.raises(sa.exc.DBAPIError):
        with bundle.engine.begin() as db:
            db.execute(table.update().where(table.c.route_policy_id==route['route_policy_id'])
                       .values(source_sha256='c'*64))
    with pytest.raises(sa.exc.DBAPIError):
        with bundle.engine.begin() as db:
            db.execute(table.delete().where(table.c.route_policy_id==route['route_policy_id']))
    with pytest.raises(sa.exc.DBAPIError):
        with bundle.engine.begin() as db:
            db.exec_driver_sql('TRUNCATE public.model_route_budget_policies')


def test_route_publication_version_currency_and_permission(queue_setup):
    bundle,tokens,budgets,_,primary,alternate=_prepare_route(queue_setup)
    publication=uuid4()
    row=_publish(budgets,publication_id=publication)
    assert _publish(budgets,publication_id=publication)['route_policy_id']==row['route_policy_id']
    with pytest.raises(CostConflict):
        _publish(budgets,publication_id=publication,output_token_limit=11)
    with pytest.raises(CostConflict):
        _publish(budgets,expected_version=0)
    with pytest.raises(CostConflict):
        _publish(budgets,expected_version=1,currency='USD')
    customer=bundle.config.authenticated(tokens['synthetic.customer']).model_budgets()
    with pytest.raises(AuthorizationError):
        customer.route_policy(provider='openai',model='synthetic-online')
    with pytest.raises(AuthorizationError):
        _publish(customer)
    with bundle.engine.connect() as db:
        table=metadata.tables['model_route_budget_policies']
        assert db.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()==1
        audit=metadata.tables['audit_logs']
        assert db.execute(sa.select(sa.func.count()).select_from(audit).where(
            audit.c.action=='cost.route_budget.publish')).scalar_one()==1


def test_route_policy_requires_global_budget_first(queue_setup):
    bundle, _, _, tokens = queue_setup
    budgets=bundle.config.authenticated(tokens['synthetic.admin']).model_budgets()
    with pytest.raises(ValueError,match='Primary tenant budget'):
        _publish(budgets)
    assert budgets.route_policy(provider='openai',model='synthetic-online') is None


def test_route_budget_admin_http_and_customer_permission(queue_setup,tmp_path):
    bundle,_,_,tokens=queue_setup
    policy=AuthPolicy('https://localhost')
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'docs','asset_dir':tmp_path/'assets',
                         'legacy_upload_dir':tmp_path/'legacy'},auth_policy=policy)
    tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        assert client.get('/api/admin/model-route-budget/openai?model=synthetic-online').status_code==401
        login=client.post('/api/auth/login',headers={'Origin':policy.origin},json={
            'username':'synthetic.customer','password':'synthetic-password-only','tenant_id':tenant})
        assert login.status_code==200
        assert client.get('/api/admin/model-route-budget/openai?model=synthetic-online').status_code==403
        client.cookies.clear()
        login=client.post('/api/auth/login',headers={'Origin':policy.origin},json={
            'username':'synthetic.admin','password':'synthetic-password-only','tenant_id':tenant})
        assert login.status_code==200
        headers={'Origin':policy.origin,'X-CSRF-Token':login.json()['csrf_token']}
        path='/api/admin/model-route-budget/openai'
        assert client.get(path+'?model=synthetic-online').json()=={'policy':None}
        values=dict(publication_id=str(uuid4()),expected_version=0,model='synthetic-online',
            currency='CNY',input_token_ceiling=100,output_token_limit=10,source_sha256='b'*64)
        assert client.put(path,headers=headers,json=values).status_code==422
        activate(bundle,tokens)
        response=client.put(path,headers=headers,json=values)
        assert response.status_code==200 and response.json()['version']==1
        assert client.get(path+'?model=synthetic-online').json()['policy']['version']==1
        assert client.put(path,headers=headers,json={**values,'publication_id':str(uuid4())}).status_code==409
        assert not (tmp_path/'unused.key').exists()


def test_route_contract_and_reservation_survive_real_encrypted_restore(backup_setup,tmp_path):
    bundle,common,package,tenant,token,_=backup_setup
    admin=bundle.config.authenticated(token)
    costs=admin.model_costs()
    for provider,model in (('deepseek','synthetic'),('openai','synthetic-online')):
        costs.publish_price(publication_id=str(uuid4()),expected_version=0,
            provider=provider,model=model,currency='CNY',source_sha256='a'*64,
            input_rate='2',output_rate='8',cache_read_rate='1',cache_write_rate='3')
    budgets,global_policy,_=activate(bundle,{'synthetic.admin':token})
    admin.update_provider('openai',model='synthetic-online',now=utc_now(),
        encrypted_key=b'synthetic-ciphertext',nonce=b'synthetic-nonce',replace_key=True)
    admin.model_fallbacks().publish(provider='openai',enabled=True,
        expected_version=0,publication_id=uuid4())
    route=_publish(budgets)
    sid,rid,owner=str(uuid4()),str(uuid4()),str(uuid4())
    chat=bundle.chat.authenticated(token)
    assert chat.start_run(session_id=sid,run_id=rid,user_id=str(uuid4()),title='Synthetic',
        content='Synthetic',provider='deepseek',model='synthetic',prompt_version_id=None,
        now=utc_now(),lease_owner=owner)
    bound=chat.with_lease(RunLease(rid,owner,1))
    alternate=bound.model_meter(rid,provider='openai',model='synthetic-online')
    alternate.begin(str(uuid4()),request=request())
    bound.finish_run(rid,'interrupted','synthetic-stop',utc_now())
    names=('model_route_budget_policies','model_budget_reservations')
    with bundle.engine.connect() as db:
        expected={name:database_manifest(db)['tables'][name] for name in names}
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        options={**common,'url':url,'knowledge_root':tmp_path/'restored-knowledge',
                 'asset_root':tmp_path/'restored-assets'}
        assert restore_backup(**options,source=package,isolated_confirmed=True)['verified']
        restored=PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            with restored.engine.connect() as db:
                actual=database_manifest(db)
                assert {name:actual['tables'][name] for name in names}==expected
                reservation=db.execute(sa.select(metadata.tables['model_budget_reservations'])).mappings().one()
                assert str(reservation['policy_id'])==global_policy['policy_id']
                assert str(reservation['route_policy_id'])==route['route_policy_id']
        finally:
            restored.close()
