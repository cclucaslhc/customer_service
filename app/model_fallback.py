"""Request-level failover; never replay an Agent run or a streamed response."""

from contextlib import asynccontextmanager
import asyncio

from pydantic_ai.models.wrapper import WrapperModel

from app.model_failures import classify_model_failure
from app.model_observability import observe_model
from app.model_budget import ModelBudgetError
from app.model_circuit import ModelCircuitOpen
from app.persistence.model_usage_repository import ModelFallbackDenied
from app.providers import ProviderError


def _eligible(exc):
    if isinstance(exc, ModelCircuitOpen):
        return True
    failure = classify_model_failure(exc)
    return failure.kind in {'transport', 'provider_unavailable', 'rate_limited'}


class FallbackModel(WrapperModel):
    def __init__(self, primary, *, gateway, run_repository, run_id, primary_provider, primary_model):
        super().__init__(primary)
        self.gateway = gateway
        self.run_repository = run_repository
        self.run_id = run_id
        self.primary_provider = primary_provider
        self.primary_model = primary_model
        self.last_provider = primary_provider
        self.last_model = primary_model

    async def _standbys(self):
        # A loopback model cannot enlarge the online data-recipient set. Online
        # fallback is considered only for DeepSeek; the attempt gate enforces
        # tenant consent, current model config, lease and budget atomically.
        choices = ['local']
        if self.primary_provider == 'deepseek':
            choices.append('openai')
        for provider in choices:
            if provider == self.primary_provider:
                continue
            try:
                snapshot = await asyncio.to_thread(self.gateway.fallback_snapshot, provider)
                if provider == 'local':
                    await self.gateway.ensure_local_tool_capability(snapshot)
                meter = self.run_repository.model_meter(
                    self.run_id, provider=snapshot.provider, model=snapshot.model)
                yield snapshot, observe_model(self.gateway.build_model(snapshot),
                    provider=snapshot.provider, meter=meter)
            except ProviderError:
                continue

    async def request(self, messages, model_settings, model_request_parameters):
        try:
            response = await self.wrapped.request(messages, model_settings, model_request_parameters)
            self.last_provider, self.last_model = self.primary_provider, self.primary_model
            return response
        except Exception as original:
            if not _eligible(original):
                raise
            last_error = original
        async for snapshot, standby in self._standbys():
            try:
                response = await standby.request(messages, model_settings, model_request_parameters)
                self.last_provider, self.last_model = snapshot.provider, snapshot.model
                return response
            except (ModelFallbackDenied, ModelBudgetError):
                # Both are raised before outbound I/O by the fenced attempt
                # gate. Another candidate may have a separate valid contract.
                continue
            except Exception as exc:
                if not _eligible(exc):
                    raise
                last_error = exc
        raise last_error

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        entered = False
        try:
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context) as response:
                entered = True
                self.last_provider, self.last_model = self.primary_provider, self.primary_model
                yield response
                return
        except Exception as original:
            if entered or not _eligible(original):
                raise
            last_error = original
        async for snapshot, standby in self._standbys():
            try:
                async with standby.request_stream(
                    messages, model_settings, model_request_parameters, run_context) as response:
                    entered = True
                    self.last_provider, self.last_model = snapshot.provider, snapshot.model
                    yield response
                    return
            except (ModelFallbackDenied, ModelBudgetError):
                if entered:
                    raise
                continue
            except Exception as exc:
                if entered or not _eligible(exc):
                    raise
                last_error = exc
        raise last_error
