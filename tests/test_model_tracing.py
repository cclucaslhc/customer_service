"""Actual SDK calls using deterministic models or private in-memory HTTP transport."""
import asyncio
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.usage import RequestUsage

from app.agent import agent, AgentDeps
from app.model_observability import observe_model
from app.providers import ModelGateway, ProviderSnapshot
from app.security import SecretStore
from app.tracing import SafeTracing, agent_scope, capture_carrier


@pytest.mark.asyncio
async def test_connect_before_send_retries_one_model_request_with_separate_meter_attempts():
    calls = 0
    class Meter:
        def __init__(self):
            self.begun = []
            self.finished = []
        def begin(self, attempt_id, *, request):
            self.begun.append(attempt_id)
            return None
        def finish(self, attempt_id, state, usage):
            self.finished.append((attempt_id, state))
    def function(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError('SECRET_NO_SEND', request=httpx.Request('POST', 'https://synthetic.invalid'))
        return ModelResponse([TextPart('safe')], usage=RequestUsage(input_tokens=2, output_tokens=1))
    meter = Meter()
    model = observe_model(FunctionModel(function), provider='synthetic', meter=meter)
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    try:
        with tracing.scope('agent.run', root=True, run_id=str(uuid4())):
            response = await model.request([], None, ModelRequestParameters())
        assert response.parts[0].content == 'safe' and calls == 2
        assert len(set(meter.begun)) == 2
        assert meter.finished == [(meter.begun[0], 'error'), (meter.begun[1], 'completed')]
        assert tracing.flush()
        spans = [span for span in exporter.get_finished_spans() if span.name == 'model.request']
        assert len(spans) == 2
        assert {span.attributes['attempt_id'] for span in spans} == set(meter.begun)
        assert 'SECRET' not in evidence(spans)
    finally:
        tracing.close()


@pytest.mark.asyncio
async def test_stream_connect_before_context_entry_retries_once_without_replaying_output(monkeypatch):
    async def stream(messages, info):
        yield 'safe'
    model = observe_model(FunctionModel(stream_function=stream), provider='synthetic')
    original = model.wrapped.request_stream
    calls = 0
    @asynccontextmanager
    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError('SECRET_NO_SEND', request=httpx.Request('POST', 'https://synthetic.invalid'))
        async with original(*args, **kwargs) as response:
            yield response
    monkeypatch.setattr(model.wrapped, 'request_stream', flaky)
    async with model.request_stream([], None, ModelRequestParameters()) as response:
        parts = [part async for part in response]
    assert calls == 2 and parts


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    ModelHTTPError(503, 'synthetic-model'),
    httpx.ReadTimeout('SECRET_UNKNOWN', request=httpx.Request('POST', 'https://synthetic.invalid')),
])
async def test_provider_response_or_read_failure_does_not_auto_retry(error):
    calls = 0
    def function(messages, info):
        nonlocal calls
        calls += 1
        raise error
    model = observe_model(FunctionModel(function), provider='synthetic')
    with pytest.raises(type(error)):
        await model.request([], None, ModelRequestParameters())
    assert calls == 1


def evidence(spans):
    return json.dumps([{'name': s.name, 'attributes': dict(s.attributes),
                       'description': s.status.description} for s in spans])


@pytest.mark.asyncio
async def test_actual_agent_tool_round_and_validation_retry_each_have_independent_request():
    calls = 0
    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse([ToolCallPart('get_business_time', {})], usage=RequestUsage(input_tokens=3))
        assert any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)
        return ModelResponse([TextPart('' if calls == 2 else '合成最终回答。')],
                             usage=RequestUsage(input_tokens=5, output_tokens=2))
    wrapped = observe_model(FunctionModel(model), provider='synthetic')
    assert observe_model(wrapped, provider='synthetic') is wrapped
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    run_id = str(uuid4())
    try:
        async with agent_scope(run_id):  # Disabled is a no-op.
            assert capture_carrier() is None
        with tracing.scope('http.request', root=True, request_id=str(uuid4())):
            async with agent_scope(run_id):
                result = await agent.run('SECRET_PROMPT', model=wrapped, deps=AgentDeps(None))
        assert result.output == '合成最终回答。'
        assert result.usage.input_tokens == 13
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        requests = [s for s in spans if s.name == 'model.request']
        parent = next(s for s in spans if s.name == 'agent.run')
        assert len(requests) == 3 and calls == 3
        assert len({s.attributes['attempt_id'] for s in requests}) == 3
        assert all(s.parent.span_id == parent.context.span_id for s in requests)
        assert all(s.attributes['run_id'] == run_id for s in requests)
        assert all(s.attributes['outcome'] == 'completed' for s in requests)
        assert all(s.attributes['model.provider'] == 'synthetic' and
                   s.attributes['model.stream'] is False for s in requests)
        assert 'SECRET' not in evidence(spans)
        # Validation is outside request: a returned empty answer is not a failed transport.
        assert capture_carrier() is None
    finally:
        tracing.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,outcome', [('drain','completed'), ('early','incomplete'),
    ('cancel_stream','cancelled'), ('cancel_task','cancelled'), ('failure','error')])
async def test_actual_stream_lifecycle_and_no_sensitive_content(mode, outcome):
    async def streaming(messages, info):
        yield 'SECRET_FIRST'
        if mode == 'failure':
            raise RuntimeError('SECRET_TRANSPORT_ERROR')
        yield 'SECRET_LAST'
    model = observe_model(FunctionModel(stream_function=streaming), provider='synthetic')
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    async def consume():
        async with model.request_stream([], None, ModelRequestParameters()) as response:
            if mode in {'drain', 'failure'}:
                async for _ in response:
                    pass
            else:
                await anext(response.__aiter__())
                if mode == 'cancel_stream':
                    await response.cancel()
                elif mode == 'cancel_task':
                    raise asyncio.CancelledError('SECRET_CANCEL_ERROR')
    try:
        with tracing.scope('agent.run', root=True, request_id=str(uuid4()), run_id=str(uuid4())):
            parent = capture_carrier()
            if mode == 'cancel_task':
                with pytest.raises(asyncio.CancelledError):
                    await consume()
            elif mode == 'failure':
                with pytest.raises(RuntimeError):
                    await consume()
            else:
                await consume()
            assert capture_carrier() == parent
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        request = next(s for s in spans if s.name == 'model.request')
        assert request.attributes['outcome'] == outcome
        assert request.attributes['model.stream'] is True
        assert (request.status.status_code.name == 'ERROR') == (outcome != 'completed')
        assert 'SECRET' not in evidence(spans)
        assert all(not s.events and not s.links for s in spans)
    finally:
        tracing.close()


@pytest.mark.asyncio
async def test_stream_teardown_failure_is_not_recorded_as_success():
    async def streaming(messages, info):
        yield 'SECRET_TEXT'
    class BrokenClose(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                yield response
            raise RuntimeError('SECRET_CLOSE_FAILURE')
    model = observe_model(BrokenClose(stream_function=streaming), provider='synthetic')
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    try:
        with tracing.scope('agent.run', root=True, run_id=str(uuid4())):
            with pytest.raises(RuntimeError):
                async with model.request_stream([], None, ModelRequestParameters()) as response:
                    async for _ in response:
                        pass
        assert tracing.flush()
        request = next(s for s in exporter.get_finished_spans() if s.name == 'model.request')
        assert request.attributes['outcome'] == 'error'
        assert 'SECRET' not in evidence(exporter.get_finished_spans())
    finally:
        tracing.close()


@pytest.mark.asyncio
async def test_gateway_actual_protocol_usage_forwarded_without_key_model_or_url_in_trace(tmp_path):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'id':'SECRET_RESPONSE_ID', 'object':'chat.completion',
            'created':1, 'model':'SECRET_MODEL', 'choices':[{'index':0,
            'message':{'role':'assistant','content':'SECRET_RESPONSE'}, 'finish_reason':'stop'}],
            'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}})
    class Repository:
        def credential_namespace(self):
            return None
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    gateway = ModelGateway(SecretStore(path=tmp_path/'unused.key'), repository=Repository(),
                           client_factory=lambda: client)
    model = gateway.build_model(ProviderSnapshot('deepseek','SECRET_MODEL','SECRET_SYNTHETIC_KEY'))
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    try:
        with tracing.scope('agent.run', root=True, request_id=str(uuid4()), run_id=str(uuid4())):
            response = await model.request([], None, ModelRequestParameters())
        assert len(calls) == 1
        assert calls[0].url.host == 'api.deepseek.com'
        assert response.usage.input_tokens == 11 and response.usage.output_tokens == 7
        assert response.parts[0].content == 'SECRET_RESPONSE'
        assert tracing.flush()
        request = next(s for s in exporter.get_finished_spans() if s.name == 'model.request')
        assert request.attributes['model.provider'] == 'deepseek'
        assert request.attributes['outcome'] == 'completed'
        assert 'SECRET' not in evidence(exporter.get_finished_spans())
        assert 'api.deepseek.com' not in evidence(exporter.get_finished_spans())
        assert not (tmp_path/'unused.key').exists()
    finally:
        tracing.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_sdk_wrapped_connect_failure_retries_at_most_once(tmp_path):
    calls = []
    def transport(request):
        calls.append(request)
        raise httpx.ConnectError('SECRET_CONNECT', request=request)
    class Repository:
        def credential_namespace(self):
            return None
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
    gateway = ModelGateway(SecretStore(path=tmp_path/'unused.key'), repository=Repository(),
                           client_factory=lambda: client)
    model = gateway.build_model(ProviderSnapshot('deepseek', 'synthetic', 'SYNTHETIC_NOT_REAL_KEY'))
    try:
        with pytest.raises(Exception):
            await model.request([], None, ModelRequestParameters())
        assert len(calls) == 2
        assert all(request.url.host == 'api.deepseek.com' for request in calls)
        assert not (tmp_path/'unused.key').exists()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_sdk_stream_connect_failure_retries_at_most_once(tmp_path):
    calls = []
    def transport(request):
        calls.append(request)
        raise httpx.ConnectError('SECRET_CONNECT', request=request)
    class Repository:
        def credential_namespace(self):
            return None
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False)
    gateway = ModelGateway(SecretStore(path=tmp_path/'unused.key'), repository=Repository(),
                           client_factory=lambda: client)
    model = gateway.build_model(ProviderSnapshot('deepseek', 'synthetic', 'SYNTHETIC_NOT_REAL_KEY'))
    try:
        with pytest.raises(Exception):
            async with model.request_stream([], None, ModelRequestParameters()):
                pass
        assert len(calls) == 2
        assert all(request.url.host == 'api.deepseek.com' for request in calls)
    finally:
        await client.aclose()


def test_registered_provider_and_idempotent_wrapper_boundary():
    model = FunctionModel(lambda messages, info: ModelResponse([TextPart('Synthetic')]))
    with pytest.raises(ValueError, match='Invalid model trace provider'):
        observe_model(model, provider='SECRET_PROVIDER')
    wrapped = observe_model(model, provider='synthetic')
    with pytest.raises(ValueError, match='provider mismatch'):
        observe_model(wrapped, provider='deepseek')
    assert wrapped.model_name == model.model_name and wrapped.profile == model.profile
