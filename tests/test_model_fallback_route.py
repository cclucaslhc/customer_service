"""Request failover tests use synthetic models and no supplier credentials."""

import httpx
import pytest
from contextlib import asynccontextmanager
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.exceptions import ModelHTTPError

from app.model_fallback import FallbackModel
from app.model_circuit import ModelCircuitOpen
from app.model_observability import observe_model
from app.persistence.model_usage_repository import ModelFallbackDenied
from app.providers import ProviderError, ProviderSnapshot


class Meter:
    def __init__(self, events, provider):
        self.events, self.provider = events, provider

    def begin(self, attempt_id, *, request):
        self.events.append((self.provider, 'begin'))
        return request[1]

    def finish(self, attempt_id, outcome, usage):
        self.events.append((self.provider, outcome))


class RunRepository:
    def __init__(self, events):
        self.events = events

    def model_meter(self, run_id, *, provider, model):
        return Meter(self.events, provider)


class Gateway:
    def __init__(self, events, *, local=True, online=True):
        self.events, self.local, self.online = events, local, online

    def fallback_snapshot(self, provider):
        self.events.append((provider, 'lookup'))
        if not (self.local if provider == 'local' else self.online):
            raise ProviderError('provider_not_configured', 'synthetic missing')
        return ProviderSnapshot(provider, 'synthetic-' + provider, 'synthetic-only')

    async def ensure_local_tool_capability(self, snapshot):
        self.events.append((snapshot.provider, 'capability'))

    def build_model(self, snapshot):
        def respond(messages, info):
            self.events.append((snapshot.provider, 'request'))
            return ModelResponse([TextPart(snapshot.provider)])
        async def stream(messages, info):
            self.events.append((snapshot.provider, 'stream'))
            yield snapshot.provider
        return FunctionModel(respond, stream_function=stream)


def route(primary, events, *, local=True, online=True, provider='deepseek'):
    model = observe_model(FunctionModel(primary), provider=provider,
                          meter=Meter(events, provider))
    return FallbackModel(model, gateway=Gateway(events, local=local, online=online),
        run_repository=RunRepository(events), run_id='synthetic', primary_provider=provider,
        primary_model='synthetic-primary')


@pytest.mark.asyncio
async def test_denied_online_attempt_never_reaches_online_model():
    events = []
    class DeniedRunRepository(RunRepository):
        def model_meter(self, run_id, *, provider, model):
            class DeniedMeter(Meter):
                def begin(self, attempt_id, *, request):
                    events.append((provider, 'denied'))
                    raise ModelFallbackDenied('synthetic consent disabled')
            return DeniedMeter(events, provider)
    primary = observe_model(FunctionModel(lambda m, i: (_ for _ in ()).throw(
        ModelHTTPError(503, 'synthetic'))), provider='deepseek')
    model = FallbackModel(primary, gateway=Gateway(events, local=False),
        run_repository=DeniedRunRepository(events), run_id='synthetic', primary_provider='deepseek',
        primary_model='synthetic-primary')
    with pytest.raises(ModelHTTPError):
        await model.request([], None, ModelRequestParameters())
    assert ('openai', 'denied') in events
    assert ('openai', 'request') not in events


@pytest.mark.asyncio
async def test_open_primary_circuit_routes_without_a_primary_paid_attempt():
    events = []
    class Circuit:
        def acquire(self):
            raise ModelCircuitOpen('synthetic open')
    class OpenMeter(Meter):
        @property
        def circuit(self):
            return Circuit()
    def primary_call(messages,info):
        events.append(('deepseek','request'))
        return ModelResponse([TextPart('should not run')])
    primary = observe_model(FunctionModel(primary_call),provider='deepseek',
        meter=OpenMeter(events,'deepseek'))
    model = FallbackModel(primary,gateway=Gateway(events),
        run_repository=RunRepository(events),run_id='synthetic',
        primary_provider='deepseek',primary_model='synthetic-primary')
    response = await model.request([],None,ModelRequestParameters())
    assert response.parts[0].content == 'local'
    assert ('deepseek','begin') not in events and ('deepseek','request') not in events


@pytest.mark.asyncio
async def test_retryable_primary_failure_prefers_loopback_and_does_not_contact_online():
    events = []
    def fail(messages, info):
        events.append(('deepseek', 'request'))
        raise ModelHTTPError(503, 'synthetic')
    model = route(fail, events)
    response = await model.request([], None, ModelRequestParameters())
    assert response.parts[0].content == 'local'
    assert (model.last_provider, model.last_model) == ('local', 'synthetic-local')
    assert ('openai', 'lookup') not in events
    assert events.index(('local', 'begin')) < events.index(('local', 'request'))


@pytest.mark.asyncio
async def test_missing_loopback_can_use_online_attempt_gate():
    events = []
    def fail(messages, info):
        raise ModelHTTPError(429, 'synthetic')
    response = await route(fail, events, local=False).request([], None, ModelRequestParameters())
    assert response.parts[0].content == 'openai'
    assert events.index(('openai', 'begin')) < events.index(('openai', 'request'))


@pytest.mark.asyncio
async def test_local_tool_probe_failure_never_sends_user_request_to_local():
    events = []
    class UnqualifiedGateway(Gateway):
        async def ensure_local_tool_capability(self, snapshot):
            events.append(('local', 'capability_denied'))
            raise ProviderError('local_capability_unverified', 'synthetic unqualified')
    primary = observe_model(FunctionModel(lambda m, i: (_ for _ in ()).throw(
        ModelHTTPError(503, 'synthetic'))), provider='deepseek')
    model = FallbackModel(primary, gateway=UnqualifiedGateway(events),
        run_repository=RunRepository(events), run_id='synthetic',
        primary_provider='deepseek', primary_model='synthetic-primary')
    response = await model.request([], None, ModelRequestParameters())
    assert response.parts[0].content == 'openai'
    assert ('local', 'capability_denied') in events
    assert ('local', 'request') not in events
    assert ('openai', 'begin') in events


@pytest.mark.asyncio
async def test_non_retryable_or_no_standby_fails_without_external_request():
    events = []
    def denied(messages, info):
        raise ModelHTTPError(401, 'synthetic')
    with pytest.raises(ModelHTTPError):
        await route(denied, events).request([], None, ModelRequestParameters())
    assert not any(event[1] == 'lookup' for event in events)
    with pytest.raises(ModelHTTPError):
        await route(lambda m, i: (_ for _ in ()).throw(ModelHTTPError(503, 'synthetic')),
            events, local=False, online=False).request([], None, ModelRequestParameters())
    assert not any(event[1] == 'request' and event[0] != 'deepseek' for event in events)


@pytest.mark.asyncio
async def test_stream_fails_over_only_before_response_context_is_entered():
    events = []
    async def primary_stream(messages, info):
        yield 'primary'
    primary = observe_model(FunctionModel(stream_function=primary_stream),
        provider='deepseek', meter=Meter(events, 'deepseek'))
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise ModelHTTPError(503, 'synthetic')
        yield  # pragma: no cover
    primary.wrapped.request_stream = unavailable
    model = FallbackModel(primary, gateway=Gateway(events),
        run_repository=RunRepository(events), run_id='synthetic', primary_provider='deepseek',
        primary_model='synthetic-primary')
    async with model.request_stream([], None, ModelRequestParameters()) as response:
        chunks = [part async for part in response]
    assert chunks and ('local', 'stream') in events
    assert ('openai', 'lookup') not in events

    events.clear()
    async def entered(*args, **kwargs):
        yield 'primary'
    primary.wrapped.request_stream = FunctionModel(stream_function=entered).request_stream
    with pytest.raises(httpx.ReadTimeout):
        async with model.request_stream([], None, ModelRequestParameters()) as response:
            raise httpx.ReadTimeout('synthetic', request=httpx.Request('POST', 'https://invalid.test'))
    assert not any(provider in {'local', 'openai'} for provider, _ in events)
