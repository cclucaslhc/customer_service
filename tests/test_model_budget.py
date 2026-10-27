"""Owned PostgreSQL + deterministic/in-memory models; no real supplier calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from app.model_budget import ModelBudgetError, ModelBudgetExceeded, bounded_settings
from app.model_observability import observe_model
from app.persistence.model_cost_repository import CostConflict
from app.persistence.schema import metadata
from app.identity import AuthorizationError, AuthenticationError
from app.agent import agent, AgentDeps
from app.backup import create_backup, restore_backup, database_manifest
from app.database import utc_now
from app.run_leases import RunLease
from app.persistence.bundle import PostgreSQLRepositories
from app.auth_http import AuthPolicy
from app.main import create_app
from app.providers import ModelGateway, ProviderSnapshot
from app.security import SecretStore
from fastapi.testclient import TestClient
from test_model_usage import prepare, queue_setup, postgres_database, backup_setup, empty_database
from test_model_cost import publish
from test_durable_chat import submit


def request(settings=None):
    return ([ModelRequest([UserPromptPart('Synthetic text')])],settings,ModelRequestParameters())


def activate(bundle,tokens,**changes):
    budgets=bundle.config.authenticated(tokens['synthetic.admin']).model_budgets()
    values=dict(publication_id=str(uuid4()),expected_version=0,provider='deepseek',model='synthetic',
        currency='CNY',tenant_limit='1',day_limit='1',run_limit='1',input_token_ceiling=100,
        output_token_limit=10,max_run_requests=10,soft_percent=80,source_sha256='e'*64)
    values.update(changes)
    return budgets,budgets.publish(**values),values


def test_request_surface_disallows_unbounded_overrides_and_media():
    assert bounded_settings(request(),10)=={'max_tokens':10}
    assert bounded_settings(request({'max_tokens':100,'temperature':0}),10)=={'max_tokens':10,'temperature':0}
    assert bounded_settings(request({'max_tokens':2}),10)=={'max_tokens':2}
    for bad in ({'extra_body':{'max_tokens':100}},{'max_tokens':None},{'max_tokens':True},
                {'max_tokens':0},{'openai_reasoning_effort':'high'}):
        with pytest.raises(ModelBudgetError):
            bounded_settings(request(bad),10)
    with pytest.raises(ModelBudgetError):
        bounded_settings(([ModelRequest([UserPromptPart(['Synthetic multimodal'])])],None,ModelRequestParameters()),10)
    with pytest.raises(ModelBudgetError):
        bounded_settings(([],None,ModelRequestParameters(allow_image_output=True)),10)
    with pytest.raises(ModelBudgetError):
        bounded_settings(None,10)


def test_atomic_reservation_limit_rollback_soft_flag_and_no_silent_release(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs(); publish(costs)
    budgets,policy,_=activate(bundle,tokens,tenant_limit='0.00038')
    attempt=str(uuid4()); settings=meter.begin(attempt,request=request())
    assert settings=={'max_tokens':10}
    rows=budgets.reservations(run_id=rid)
    assert len(rows)==1 and rows[0]['amount']==Decimal('.000380000') and rows[0]['tenant_soft'] is True
    assert rows[0]['day_soft'] is False and rows[0]['policy_id']==policy['policy_id']
    # A successful low observed cost still does not silently refund worst-case cap.
    meter.finish(attempt,'completed',RequestUsage(input_tokens=1,output_tokens=1))
    costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=0,
        amount='0',currency='CNY',receipt_sha256='f'*64)
    denied=str(uuid4())
    with pytest.raises(ModelBudgetExceeded):
        meter.begin(denied,request=request())
    assert len(meter.list_attempts())==1 and len(budgets.reservations(run_id=rid))==1
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(metadata.tables['model_price_quotes'].c.attempt_id).where(
            metadata.tables['model_price_quotes'].c.attempt_id==UUID(denied))).first() is None


def test_independent_runs_concurrent_tenant_gate_prevents_double_spend(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    budgets,_,_=activate(bundle,tokens,tenant_limit='0.00038')
    sid2,rid2=submit(owner); job2=root.claim_next()
    meter2=root.authenticated(job2['credential']).with_lease(job2['lease']).model_meter(rid2,provider='deepseek',model='synthetic')
    def call(bound):
        try:
            bound.begin(str(uuid4()),request=request())
            return 'reserved'
        except ModelBudgetExceeded:
            return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(call,(meter,meter2)))==['denied','reserved']
    assert len(budgets.reservations(run_id=rid))+len(budgets.reservations(run_id=rid2))==1


@pytest.mark.parametrize('scope', ['day_limit','run_limit'])
def test_day_and_run_limits_are_enforced_independently(queue_setup,scope):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    activate(bundle,tokens,**{scope:'0.000379999'})
    with pytest.raises(ModelBudgetExceeded):
        meter.begin(str(uuid4()),request=request())
    assert meter.list_attempts()==[]


def test_policy_price_unknown_history_and_supported_call_required(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    budgets,_,_=activate(bundle,tokens)
    with pytest.raises(ModelBudgetError):
        meter.begin(str(uuid4()),request=request())  # no matching pinned price
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    with pytest.raises(ModelBudgetError):
        meter.begin(str(uuid4()))  # bypassing request-bound output checks
    with pytest.raises(ModelBudgetError):
        meter.begin(str(uuid4()),request=request({'extra_body':{}}))
    assert meter.list_attempts()==[]


def test_pre_policy_unknown_request_blocks_new_calls_even_after_operator_statement(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs(); publish(costs)
    unknown=str(uuid4()); meter.begin(unknown)
    costs.reconcile(attempt_id=unknown,request_id=str(uuid4()),expected_revision=0,
        amount='0',currency='CNY',receipt_sha256='f'*64)
    activate(bundle,tokens)
    with pytest.raises(ModelBudgetError):
        meter.begin(str(uuid4()),request=request())
    assert len(meter.list_attempts())==1


@pytest.mark.asyncio
async def test_real_sdk_bound_settings_committed_before_call_and_repeated_call_refused(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    budgets,_,_=activate(bundle,tokens,max_run_requests=1)
    calls=0
    class BoundModel(FunctionModel):
        async def request(self,messages,settings,parameters):
            nonlocal calls
            calls+=1
            assert settings['max_tokens']==10
            assert len(budgets.reservations(run_id=rid))==1  # independent connection sees commit
            return ModelResponse([TextPart('Synthetic answer')],usage=RequestUsage(input_tokens=1,output_tokens=1))
    model=observe_model(BoundModel(lambda *_:None),provider='deepseek',meter=meter)
    await model.request([],{'max_tokens':100},ModelRequestParameters())
    with pytest.raises(ModelBudgetExceeded):
        await model.request([],None,ModelRequestParameters())
    assert calls==1


def test_budget_policy_publication_version_audit_authority_and_sql_immutability(queue_setup,monkeypatch):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    budgets,first,values=activate(bundle,tokens)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    meter.begin(str(uuid4()),request=request())
    assert budgets.publish(**values)['policy_id']==first['policy_id']
    with pytest.raises(CostConflict):
        budgets.publish(**{**values,'tenant_limit':'2'})
    with pytest.raises(CostConflict):
        budgets.publish(**{**values,'publication_id':str(uuid4())})
    with pytest.raises(CostConflict):
        budgets.publish(**{**values,'publication_id':str(uuid4()),'expected_version':1,'currency':'USD'})
    with pytest.raises(ValueError):
        budgets.publish(**{**values,'max_run_requests':True})
    with pytest.raises(AuthorizationError):
        bundle.config.authenticated(tokens['synthetic.customer']).model_budgets().publish(**values)
    def broken(*args):
        raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(budgets,'_audit',broken)
    with pytest.raises(RuntimeError):
        budgets.publish(**{**values,'publication_id':str(uuid4()),'expected_version':1})
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['model_budget_policies'])).scalar_one()==1
    for name in ('model_budget_policies','model_budget_reservations'):
        for sql in ('TRUNCATE public.'+name+' CASCADE','DELETE FROM public.'+name):
            with bundle.engine.begin() as db:
                with pytest.raises(sa.exc.DBAPIError,match='append-only'):
                    db.exec_driver_sql(sql)
    bundle.identity.logout(tokens['synthetic.admin'])
    with pytest.raises(AuthenticationError):
        budgets.publish(**values)


@pytest.mark.asyncio
async def test_stream_cap_defaults_cannot_bypass_budget_and_stream_not_opened_when_denied(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    budgets,_,_=activate(bundle,tokens,max_run_requests=1)
    calls=0
    async def stream(messages,info):
        yield 'Synthetic stream'
    class StreamModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self,messages,settings,parameters,run_context=None):
            nonlocal calls
            calls+=1
            assert settings['max_tokens']==10
            assert len(budgets.reservations(run_id=rid))==1
            async with super().request_stream(messages,settings,parameters,run_context) as response:
                yield response
    wrapped=observe_model(StreamModel(stream_function=stream,settings={'max_tokens':100}),provider='deepseek',meter=meter)
    async with wrapped.request_stream([],None,ModelRequestParameters()) as response:
        async for _ in response:
            pass
    with pytest.raises(ModelBudgetExceeded):
        async with wrapped.request_stream([],None,ModelRequestParameters()):
            pytest.fail('Denied stream must not open')
    assert calls==1
    unsupported=observe_model(FunctionModel(lambda *_:None,settings={'extra_body':{'n':2}}),provider='deepseek',meter=meter)
    with pytest.raises(ModelBudgetError):
        await unsupported.request([],None,ModelRequestParameters())


@pytest.mark.asyncio
async def test_actual_pydantic_agent_tool_loop_gets_two_independent_reservations(queue_setup):
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    budgets,_,_=activate(bundle,tokens)
    calls=0
    def function(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            return ModelResponse([ToolCallPart('get_business_time',{})],usage=RequestUsage(input_tokens=1,output_tokens=1))
        assert any(isinstance(part,ToolReturnPart) for message in messages for part in message.parts)
        return ModelResponse([TextPart('合成预算工具回答。')],usage=RequestUsage(input_tokens=2,output_tokens=1))
    wrapped=observe_model(FunctionModel(function),provider='deepseek',meter=meter)
    result=await agent.run('Synthetic time',model=wrapped,deps=AgentDeps(None))
    assert result.output=='合成预算工具回答。' and calls==2
    assert len(budgets.reservations(run_id=rid))==2


def test_zero_price_still_has_request_limit_and_observed_overage_blocks_followup(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    costs.publish_price(publication_id=str(uuid4()),expected_version=0,provider='deepseek',model='synthetic',
        currency='CNY',source_sha256='a'*64,input_rate='0',output_rate='0',cache_read_rate='0',cache_write_rate='0')
    budgets,_,values=activate(bundle,tokens,tenant_limit='0',day_limit='0',run_limit='0',max_run_requests=1)
    attempt=str(uuid4()); meter.begin(attempt,request=request()); meter.finish(attempt,'cancelled')
    assert budgets.reservations(run_id=str(meter.run_id))[0]['amount']==0
    with pytest.raises(ModelBudgetExceeded):
        meter.begin(str(uuid4()),request=request())
    publish(costs,version=1)
    budgets.publish(**{**values,'publication_id':str(uuid4()),'expected_version':1,
        'tenant_limit':'0.001','day_limit':'0.001','run_limit':'0.001','max_run_requests':10})
    over=str(uuid4()); meter.begin(over,request=request())
    meter.finish(over,'completed',RequestUsage(input_tokens=10000,output_tokens=100))
    with pytest.raises(ModelBudgetExceeded):
        meter.begin(str(uuid4()),request=request())


def test_concurrent_policy_revision_only_one_wins(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    budgets,_,values=activate(bundle,tokens)
    def update(_):
        try:
            return budgets.publish(**{**values,'publication_id':str(uuid4()),'expected_version':1})['version']
        except CostConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(update,range(2)))
    assert results.count(2)==1 and results.count('conflict')==1


def test_actual_encrypted_recovery_preserves_policy_and_unresolved_reservation(backup_setup,tmp_path):
    bundle,common,package,tenant,token,_=backup_setup
    publish(bundle.config.authenticated(token).model_costs())
    budgets,_,_=activate(bundle,{'synthetic.admin':token})
    sid,rid,owner=str(uuid4()),str(uuid4()),str(uuid4())
    chat=bundle.chat.authenticated(token)
    assert chat.start_run(session_id=sid,run_id=rid,user_id=str(uuid4()),title='Synthetic',content='Synthetic',
        provider='deepseek',model='synthetic',prompt_version_id=None,now=utc_now(),lease_owner=owner)
    bound=chat.with_lease(RunLease(rid,owner,1)); meter=bound.model_meter(rid,provider='deepseek',model='synthetic')
    attempt=str(uuid4()); meter.begin(attempt,request=request())
    meter.finish(attempt,'cancelled')
    bound.finish_run(rid,'interrupted','cancelled',utc_now())
    names=('model_budget_policies','model_budget_reservations')
    with bundle.engine.connect() as db:
        expected={name:database_manifest(db)['tables'][name] for name in names}
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        options={**common,'url':url,'knowledge_root':tmp_path/'restored-knowledge','asset_root':tmp_path/'restored-assets'}
        assert restore_backup(**options,source=package,isolated_confirmed=True)['verified']
        restored=PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            with restored.engine.connect() as db:
                actual=database_manifest(db)
                assert {name:actual['tables'][name] for name in names}==expected
                row=db.execute(sa.select(metadata.tables['model_budget_reservations'])).mappings().one()
                assert row['amount']==Decimal('.000380000')
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one() is True
        finally:
            restored.close()


def test_authenticated_http_budget_error_resets_answer_without_provider_call(queue_setup,tmp_path,monkeypatch):
    bundle,root,owner,tokens=queue_setup
    publish(bundle.config.authenticated(tokens['synthetic.admin']).model_costs())
    activate(bundle,tokens,tenant_limit='0')
    calls=0
    async def stream(messages,info):
        nonlocal calls
        calls+=1
        yield 'Should never be sent'
    monkeypatch.setattr(ModelGateway,'snapshot',lambda gateway:ProviderSnapshot('deepseek','synthetic','unused',
        gateway.repository.credential_namespace()))
    monkeypatch.setattr(ModelGateway,'build_model',lambda *_:FunctionModel(stream_function=stream))
    def forbidden(*args,**kwargs):
        raise AssertionError('Real model client forbidden')
    monkeypatch.setattr(ModelGateway,'_client',forbidden)
    policy=AuthPolicy('https://localhost')
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'docs','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'},
        auth_policy=policy)
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
        login=client.post('/api/auth/login',headers={'Origin':policy.origin},json={
            'username':'synthetic.customer','password':'synthetic-password-only','tenant_id':tenant})
        assert login.status_code==200
        headers={'Origin':policy.origin,'X-CSRF-Token':login.json()['csrf_token']}
        sid=client.post('/api/chat/sessions',headers=headers).json()['id']
        response=client.post(f'/api/chat/sessions/{sid}/messages',headers=headers,json={'content':'Synthetic request'})
        assert response.status_code==200 and 'budget_exceeded' in response.text
        assert 'event: answer_reset' in response.text and 'event: done' not in response.text
        assert '供应商配置' not in response.text and 'Should never be sent' not in response.text and calls==0
        history=client.get(f'/api/chat/sessions/{sid}').json()
        assert [m['role'] for m in history['messages']]==['user']
    assert not (tmp_path/'unused.key').exists()
