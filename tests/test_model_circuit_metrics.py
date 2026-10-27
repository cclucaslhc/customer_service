"""Private circuit aggregates expose no tenant, model or user dimension."""

import httpx
import pytest

from app.http_observability import HttpTelemetry
from app.model_circuit_metrics import ModelCircuitMetrics
from app.private_metrics import MetricsPolicy, private_metrics_listener
from app.main import create_app
from app.security import SecretStore
from test_model_usage import prepare, queue_setup, postgres_database


def test_durable_circuit_metrics_and_private_assembly(queue_setup,tmp_path):
    bundle, _, _, tokens, _, rid, _, _, meter = prepare(queue_setup)
    collector = ModelCircuitMetrics(bundle.engine)
    assert collector.snapshot()['states'] == {'closed':0,'open':0,'half_open':0}
    for _ in range(3):
        permit = meter.circuit.acquire()
        meter.circuit.report(permit,healthy=False,failure=True)
    value = collector.snapshot()
    assert value['success'] == 1 and value['openings'] == 1
    assert value['states'] == {'closed':0,'open':1,'half_open':0}
    telemetry = HttpTelemetry()
    telemetry.circuit_metrics = collector
    content = telemetry.prometheus()
    assert 'customer_service_model_circuit_collection_success 1' in content
    assert 'customer_service_model_circuit_states{state="open"} 1' in content
    assert 'customer_service_model_circuit_openings_total 1' in content
    for private in (tokens['synthetic.admin'],tokens['synthetic.customer'],rid,
                    bundle.identity.resolve(tokens['synthetic.admin']).tenant_id,'synthetic','deepseek'):
        assert private not in content
    app = create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        metrics_policy=MetricsPolicy(token=b'synthetic-circuit-metrics-token-012345678901234567890'))
    assert app.state.http_telemetry.circuit_metrics is not None


def test_failed_circuit_collection_is_visible_without_database_error(queue_setup,monkeypatch):
    collector = ModelCircuitMetrics(queue_setup[0].engine)
    def broken(*args,**kwargs):
        raise RuntimeError('SECRET_DATABASE_ERROR')
    monkeypatch.setattr(queue_setup[0].engine,'begin',broken)
    content = '\n'.join(collector.prometheus())
    assert 'customer_service_model_circuit_collection_success 0' in content
    assert 'customer_service_model_circuit_openings_total 0' in content
    assert 'SECRET' not in content


@pytest.mark.asyncio
async def test_authenticated_loopback_scrape_includes_circuit_aggregate(queue_setup):
    bundle, _, _, _, _, _, _, _, meter = prepare(queue_setup)
    for _ in range(3):
        permit = meter.circuit.acquire()
        meter.circuit.report(permit,healthy=False,failure=True)
    telemetry = HttpTelemetry()
    telemetry.circuit_metrics = ModelCircuitMetrics(bundle.engine)
    token = b'synthetic-circuit-metrics-token-012345678901234567890'
    async with private_metrics_listener(telemetry,MetricsPolicy(token=token)) as port:
        async with httpx.AsyncClient(trust_env=False) as client:
            url = f'http://127.0.0.1:{port}/metrics'
            assert (await client.get(url)).status_code == 401
            response = await client.get(url,headers={'Authorization':'Bearer '+token.decode()})
            assert response.status_code == 200
            assert 'customer_service_model_circuit_states{state="open"} 1' in response.text
            assert 'customer_service_model_circuit_openings_total 1' in response.text
