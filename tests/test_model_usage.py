"""Durable usage on owned PostgreSQL, deterministic SDK calls, no real credentials."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4, UUID

import pytest
import sqlalchemy as sa
import httpx
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from app.model_observability import observe_model
from app.persistence.model_usage_repository import usage_observation
from app.maintenance import Maintenance
from app.run_leases import RunLease, RunLeaseLost
from app.identity import AuthenticationError
from app.database import utc_now
from app.backup import create_backup, restore_backup, database_manifest
from app.persistence.bundle import PostgreSQLRepositories
from app.providers import ModelGateway, ProviderSnapshot
from app.security import SecretStore
from test_logical_backup import backup_setup, empty_database
from test_durable_chat import queue_setup, submit, postgres_database


def prepare(queue_setup):
    bundle, root, owner, tokens = queue_setup
    sid,rid=submit(owner)
    job=root.claim_next()
    chat=root.authenticated(job['credential']).with_lease(job['lease'])
    meter=chat.model_meter(rid,provider='deepseek',model='synthetic')
    return bundle,root,owner,tokens,sid,rid,job,chat,meter


def test_usage_numeric_validation_zero_defaults_and_partial_are_conservative():
    for usage in (None,RequestUsage(),SimpleNamespace(input_tokens=True),
        RequestUsage(input_tokens=-1), RequestUsage(input_tokens=2,cache_read_tokens=3),
        RequestUsage(input_tokens=2**63)):
        value=usage_observation(usage,'completed')
        assert value['usage_status']=='unknown' and value['input_tokens'] is None
    assert usage_observation(RequestUsage(input_tokens=12,output_tokens=3),'completed')['usage_status']=='observed'
    assert usage_observation(RequestUsage(input_tokens=12,output_tokens=3),'cancelled')['usage_status']=='partial'


@pytest.mark.asyncio
async def test_actual_model_precall_commit_and_completion_without_tracing(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    def model(messages,info):
        # Separate connection proves the started row committed before invocation.
        with bundle.engine.connect() as db:
            row=db.execute(sa.select(meter.table).where(meter.table.c.run_id==UUID(rid))).mappings().one()
            assert row['state']=='started' and row['usage_status']=='unknown'
            assert row['input_tokens'] is None
        return ModelResponse([TextPart('SECRET_RESPONSE')],usage=RequestUsage(input_tokens=11,output_tokens=7,cache_read_tokens=4))
    result=await observe_model(FunctionModel(model),provider='deepseek',meter=meter).request([],None,ModelRequestParameters())
    assert result.parts[0].content=='SECRET_RESPONSE'
    rows=meter.list_attempts()
    assert len(rows)==1 and rows[0]['state']=='completed' and rows[0]['input_tokens']==11
    assert rows[0]['output_tokens']==7 and rows[0]['cache_read_tokens']==4
    assert 'SECRET' not in str(rows)
    assert rows[0]['actor_id']==bundle.identity.resolve(tokens['synthetic.customer']).user_id
    meter.finish(rows[0]['attempt_id'],'completed',RequestUsage(input_tokens=11,output_tokens=7,cache_read_tokens=4))
    with pytest.raises(ValueError,match='conflict'):
        meter.finish(rows[0]['attempt_id'],'completed',RequestUsage(input_tokens=12,output_tokens=7))


@pytest.mark.asyncio
async def test_connect_before_send_retry_has_two_durable_attempts(queue_setup):
    *_, meter = prepare(queue_setup)
    calls = 0
    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError('SECRET_NO_SEND', request=httpx.Request('POST', 'https://synthetic.invalid'))
        return ModelResponse([TextPart('Synthetic')], usage=RequestUsage(input_tokens=3, output_tokens=2))
    await observe_model(FunctionModel(model), provider='deepseek', meter=meter).request(
        [], None, ModelRequestParameters())
    rows = meter.list_attempts()
    assert calls == 2 and len(rows) == 2
    assert rows[0]['attempt_id'] != rows[1]['attempt_id']
    assert rows[0]['state'] == 'error' and rows[0]['usage_status'] == 'unknown'
    assert rows[1]['state'] == 'completed' and rows[1]['usage_status'] == 'observed'
    assert 'SECRET' not in str(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,state', [('zero','completed'),('error','error'),('cancel','cancelled')])
async def test_unknown_usage_never_becomes_zero_cost_or_silent_success(queue_setup,mode,state):
    *_,meter=prepare(queue_setup)
    async def model(messages,info):
        if mode=='error':
            raise RuntimeError('SECRET_PROVIDER_ERROR')
        if mode=='cancel':
            raise asyncio.CancelledError('SECRET_CANCEL')
        return ModelResponse([TextPart('Synthetic')],usage=RequestUsage())
    class ZeroUsage(FunctionModel):
        async def request(self, *args, **kwargs):
            # Bypass FunctionModel's automatic estimate to exercise SDK zero defaults.
            return ModelResponse([TextPart('Synthetic')],usage=RequestUsage())
    raw=ZeroUsage(model) if mode=='zero' else FunctionModel(model)
    wrapped=observe_model(raw,provider='deepseek',meter=meter)
    if mode=='error':
        with pytest.raises(RuntimeError):
            await wrapped.request([],None,ModelRequestParameters())
    elif mode=='cancel':
        with pytest.raises(asyncio.CancelledError):
            await wrapped.request([],None,ModelRequestParameters())
    else:
        await wrapped.request([],None,ModelRequestParameters())
    row=meter.list_attempts()[0]
    assert row['state']==state and row['usage_status']=='unknown'
    assert row['input_tokens'] is None and row['output_tokens'] is None
    assert 'SECRET' not in str(row)


def test_database_immutability_scope_stale_lease_and_maintenance_guard(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    attempt=str(uuid4()); meter.begin(attempt)
    other=root.authenticated(tokens['synthetic.other']).with_lease(job['lease']).model_meter(rid,provider='deepseek',model='synthetic')
    with pytest.raises(RunLeaseLost):
        other.list_attempts()
    stale=root.authenticated(job['credential']).with_lease(RunLease(rid,str(uuid4()),1)).model_meter(rid,provider='deepseek',model='synthetic')
    with pytest.raises(RunLeaseLost):
        stale.begin(str(uuid4()))
    with pytest.raises(sa.exc.IntegrityError):
        meter.begin(attempt)
    meter.finish(attempt,'completed',RequestUsage(input_tokens=4,output_tokens=2))
    with bundle.engine.begin() as db:
        with pytest.raises(sa.exc.DBAPIError,match='cannot be rewritten'):
            db.execute(meter.table.update().where(meter.table.c.attempt_id==UUID(attempt)).values(input_tokens=999))
    with bundle.engine.begin() as db:
        with pytest.raises(sa.exc.DBAPIError,match='cannot be deleted'):
            db.execute(meter.table.delete().where(meter.table.c.attempt_id==UUID(attempt)))
    manager=Maintenance(bundle.engine); permit=manager.request()
    try:
        with bundle.engine.begin() as db:
            with pytest.raises(sa.exc.DBAPIError,match='maintenance write hold'):
                db.execute(meter.table.update().where(sa.false()).values(state='error'))
    finally:
        manager.release(permit)


@pytest.mark.asyncio
async def test_revoked_identity_prevents_paid_call_and_finishing_preserves_unresolved_attempt(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    attempt=str(uuid4()); meter.begin(attempt)
    bundle.identity.logout(job['credential'])
    called=False
    def model(messages,info):
        nonlocal called
        called=True
        return ModelResponse([TextPart('Should not be called')])
    with pytest.raises(AuthenticationError):
        await observe_model(FunctionModel(model),provider='deepseek',meter=meter).request([],None,ModelRequestParameters())
    assert called is False
    with pytest.raises(AuthenticationError):
        meter.finish(attempt,'completed',RequestUsage(input_tokens=2))
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(meter.table).where(meter.table.c.attempt_id==UUID(attempt))).mappings().one()
    assert row['state']=='started' and row['usage_status']=='unknown'


@pytest.mark.asyncio
async def test_postcall_database_failure_does_not_authorize_second_model_request(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    calls=0
    def model(messages,info):
        nonlocal calls
        calls+=1
        # Simulate lease lost after external work; no finalization privilege bypass.
        with bundle.engine.begin() as db:
            runs=root.tables['agent_runs']
            db.execute(runs.update().where(runs.c.id==UUID(rid)).values(lease_fence=job['lease'].fence+1))
        return ModelResponse([TextPart('SECRET_EXTERNAL_SUCCESS')],usage=RequestUsage(input_tokens=3))
    with pytest.raises(RunLeaseLost):
        await observe_model(FunctionModel(model),provider='deepseek',meter=meter).request([],None,ModelRequestParameters())
    assert calls==1
    with bundle.engine.connect() as db:
        rows=db.execute(sa.select(meter.table).where(meter.table.c.run_id==UUID(rid))).mappings().all()
    assert len(rows)==1 and rows[0]['state']=='started' and rows[0]['input_tokens'] is None


def test_actual_encrypted_backup_restores_usage_evidence_and_immutability(backup_setup,tmp_path):
    bundle,common,package,tenant,token,_=backup_setup
    sid,rid,owner=str(uuid4()),str(uuid4()),str(uuid4())
    chat=bundle.chat.authenticated(token)
    assert chat.start_run(session_id=sid,run_id=rid,user_id=str(uuid4()),title='Synthetic',
        content='Synthetic',provider='deepseek',model='synthetic',prompt_version_id=None,
        now=utc_now(),lease_owner=owner)
    bound=chat.with_lease(RunLease(rid,owner,1))
    meter=bound.model_meter(rid,provider='deepseek',model='synthetic')
    attempt=str(uuid4()); meter.begin(attempt)
    meter.finish(attempt,'completed',RequestUsage(input_tokens=7,output_tokens=4))
    bound.finish_run(rid,'complete',None,utc_now())
    with bundle.engine.connect() as db:
        expected=database_manifest(db)['tables']['model_usage_attempts']
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        options={**common,'url':url,'knowledge_root':tmp_path/'restored-knowledge','asset_root':tmp_path/'restored-assets'}
        result=restore_backup(**options,source=package,isolated_confirmed=True)
        assert result['verified'] and not result['workers_enabled']
        restored=PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            with restored.engine.connect() as db:
                assert database_manifest(db)['tables']['model_usage_attempts']==expected
                table=restored.chat.tables['model_usage_attempts']
                row=db.execute(sa.select(table).where(table.c.attempt_id==UUID(attempt))).mappings().one()
                assert row['input_tokens']==7 and row['output_tokens']==4
            with restored.engine.begin() as db:
                with pytest.raises(sa.exc.DBAPIError,match='cannot be rewritten'):
                    db.execute(table.update().values(input_tokens=99))
        finally:
            restored.close()


@pytest.mark.asyncio
async def test_actual_gateway_http_failure_is_single_attempt_with_unknown_usage(queue_setup,tmp_path):
    *_,meter=prepare(queue_setup)
    calls=0
    def transport(request):
        nonlocal calls
        calls+=1
        return httpx.Response(500,json={'error':{'message':'SECRET_FAILURE','type':'server_error'}})
    class Config:
        def credential_namespace(self):
            return None
    client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
    gateway=ModelGateway(SecretStore(path=tmp_path/'unused.key'),repository=Config(),client_factory=lambda:client)
    raw=gateway.build_model(ProviderSnapshot('deepseek','synthetic','SYNTHETIC_NOT_A_REAL_KEY'))
    try:
        with pytest.raises(Exception):
            await observe_model(raw,provider='deepseek',meter=meter).request([],None,ModelRequestParameters())
        assert calls==1 and raw.wrapped.provider.client.max_retries==0
        rows=meter.list_attempts()
        assert len(rows)==1 and rows[0]['state']=='error' and rows[0]['usage_status']=='unknown'
        assert 'SECRET' not in str(rows)
    finally:
        await client.aclose()
