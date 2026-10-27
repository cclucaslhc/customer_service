"""Durable aggregate budget telemetry on owned PostgreSQL; no tenant labels."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.usage import RequestUsage

from app.database import utc_now
from app.http_observability import HttpTelemetry
from app.model_budget import ModelBudgetExceeded
from app.model_budget_metrics import ModelBudgetMetrics
from app.private_metrics import MetricsPolicy
from app.main import create_app
from app.auth_http import AuthPolicy
from app.security import SecretStore
from test_model_budget import activate, request
from test_model_cost import publish
from test_model_usage import prepare, queue_setup, postgres_database


def test_durable_metrics_are_aggregate_monotonic_and_hide_business_dimensions(queue_setup,tmp_path):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    telemetry=HttpTelemetry(); collector=ModelBudgetMetrics(bundle.engine)
    telemetry.budget_metrics=collector
    initial=collector.snapshot()
    assert initial['success']==1 and initial['policies']==initial['reservations']==0
    assert initial['unresolved_tenants']==0
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs(); publish(costs)
    budgets,_,_=activate(bundle,tokens,tenant_limit='0.00038',day_limit='0.00038')
    attempt=str(uuid4()); meter.begin(attempt,request=request())
    meter.finish(attempt,'completed',RequestUsage(input_tokens=1,output_tokens=1))
    with pytest.raises(ModelBudgetExceeded):
        meter.begin(str(uuid4()),request=request())
    chat.finish_run(rid,'error','budget_exceeded',utc_now())
    value=collector.snapshot()
    assert value['policies']==value['reservations']==1
    assert value['soft']=={'tenant':1,'day':1,'run':0}
    assert value['rejections']=={'exceeded':1,'unavailable':0}
    assert value['unresolved_tenants']==0
    content=telemetry.prometheus()
    assert 'customer_service_model_budget_collection_success 1' in content
    assert 'customer_service_model_budget_soft_threshold_events_total{scope="tenant"} 1' in content
    assert 'customer_service_model_budget_rejections_total{reason="exceeded"} 1' in content
    forbidden=(tokens['synthetic.admin'],tokens['synthetic.customer'],rid,attempt,
               bundle.identity.resolve(tokens['synthetic.admin']).tenant_id,'synthetic','CNY','0.000380000')
    assert all(secret not in content for secret in forbidden)
    # Explicit authenticated private-metrics assembly installs the collector;
    # public business routes still do not expose /metrics.
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        auth_policy=None,http_telemetry=telemetry,
        metrics_policy=MetricsPolicy(token=b'synthetic-budget-metrics-token-012345678901234567890'))
    assert app.state.http_telemetry.budget_metrics is not None


def test_unreserved_unknown_attempt_is_counted_only_for_protected_tenant(queue_setup):
    bundle,*_,meter=prepare(queue_setup)
    meter.begin(str(uuid4()))
    value=ModelBudgetMetrics(bundle.engine).snapshot()
    assert value['success']==1 and value['unresolved_tenants']==0
    assert value['policies']==value['reservations']==0
    activate(bundle,queue_setup[3])
    value=ModelBudgetMetrics(bundle.engine).snapshot()
    assert value['unresolved_tenants']==1
    status=bundle.config.authenticated(queue_setup[3]['synthetic.admin']).model_budgets().status()
    assert status['usage']['unresolved'] is True
    assert status['usage']['tenant_amount'] is None and status['usage']['day_amount'] is None


def test_collector_failure_is_a_safe_metric_not_raw_exception(queue_setup,monkeypatch):
    bundle=queue_setup[0]; collector=ModelBudgetMetrics(bundle.engine)
    def unavailable(*args,**kwargs):
        raise RuntimeError('SECRET_DATABASE_FAILURE')
    monkeypatch.setattr(bundle.engine,'begin',unavailable)
    content='\n'.join(collector.prometheus())
    assert 'customer_service_model_budget_collection_success 0' in content
    assert 'SECRET' not in content


def test_authenticated_admin_price_budget_api_decimal_contract_and_role(queue_setup,tmp_path):
    bundle,root,owner,tokens=queue_setup
    policy=AuthPolicy('https://localhost')
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        auth_policy=policy,knowledge_paths={'knowledge_dir':tmp_path/'docs','asset_dir':tmp_path/'assets',
        'legacy_upload_dir':tmp_path/'legacy'})
    with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
        tenant=bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
        def login(username):
            client.cookies.clear()
            response=client.post('/api/auth/login',headers={'Origin':policy.origin},json={
                'username':username,'password':'synthetic-password-only','tenant_id':tenant})
            assert response.status_code==200
            return {'Origin':policy.origin,'X-CSRF-Token':response.json()['csrf_token']}
        customer=login('synthetic.customer')
        assert client.get('/api/admin/model-budget').status_code==403
        admin=login('synthetic.admin')
        assert client.get('/api/admin/model-budget').json()=={
            'enabled':False,'policy':None,'usage':None,'soft_events':{'tenant':0,'day':0,'run':0}}
        publication=str(uuid4())
        price={'publication_id':publication,'expected_version':0,'model':'synthetic','currency':'CNY',
            'input_rate':'2','output_rate':'8','cache_read_rate':'1','cache_write_rate':'3','source_sha256':'a'*64}
        saved=client.put('/api/admin/model-prices/deepseek',headers=admin,json=price)
        assert saved.status_code==200 and saved.json()['input_rate']=='2.000000000'
        assert client.put('/api/admin/model-prices/deepseek',headers=admin,json=price).json()['price_id']==saved.json()['price_id']
        assert client.put('/api/admin/model-prices/deepseek',headers=admin,json={**price,'input_rate':'3'}).status_code==409
        assert client.put('/api/admin/model-prices/deepseek',headers=admin,json={**price,'publication_id':str(uuid4()),
            'expected_version':1,'input_rate':0.1}).status_code==422
        current=client.get('/api/admin/model-prices/deepseek?model=synthetic').json()['price']
        assert current['input_rate']=='2.000000000' and current['source_sha256']=='a'*64
        budget={'publication_id':str(uuid4()),'expected_version':0,'provider':'deepseek','model':'synthetic',
            'currency':'CNY','tenant_limit':'1','day_limit':'0.5','run_limit':'0.1',
            'input_token_ceiling':100,'output_token_limit':10,'max_run_requests':4,'soft_percent':80,
            'source_sha256':'b'*64}
        created=client.put('/api/admin/model-budget',headers=admin,json=budget)
        assert created.status_code==200 and created.json()['day_limit']=='0.500000000'
        status=client.get('/api/admin/model-budget').json()
        assert status['enabled'] is True and status['policy']['version']==1
        assert status['usage']=={'tenant_amount':'0.000000000','day_amount':'0.000000000',
            'tenant_remaining':'1.000000000','day_remaining':'0.500000000','unresolved':False}
        assert client.put('/api/admin/model-budget',headers=admin,json={**budget,'soft_percent':100}).status_code==422
    assert not (tmp_path/'unused.key').exists()
