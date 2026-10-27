"""PydanticAI model-boundary spans, with no prompt/response/URL/key extraction."""
from contextlib import asynccontextmanager
import asyncio
from uuid import uuid4

from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.exceptions import RunCancelled

from app.tracing import model_request_scope, finish_model_request
from app.model_failures import classify_model_failure


def _outcome(state):
    # A suspended provider job or early stream close is not a completed request.
    return 'completed' if state == 'complete' else 'cancelled' if state == 'interrupted' else 'incomplete'


class ObservedModel(WrapperModel):
    def __init__(self, wrapped, *, provider, meter=None):
        if provider not in {'deepseek', 'openai', 'local', 'synthetic', 'unknown'}:
            raise ValueError('Invalid model trace provider')
        super().__init__(wrapped)
        self.trace_provider = provider
        self.meter = meter

    async def _begin(self, attempt_id, messages, settings, parameters):
        if self.meter is not None:
            settings = {**(self.wrapped.settings or {}), **(settings or {})} or None
            return await asyncio.to_thread(self.meter.begin, attempt_id,
                                           request=(messages,settings,parameters,self.wrapped.profile))
        return settings

    async def _finish(self, attempt_id, outcome, response=None):
        if self.meter is None:
            return
        usage = getattr(response, 'usage', None)
        task = asyncio.create_task(asyncio.to_thread(self.meter.finish,attempt_id,outcome,usage))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _admit(self, attempt_id, messages, settings, parameters):
        circuit = self.meter.circuit if self.meter is not None and hasattr(self.meter, 'circuit') else None
        permit = await asyncio.to_thread(circuit.acquire) if circuit is not None else None
        try:
            chosen = await self._begin(attempt_id,messages,settings,parameters)
        except BaseException:
            if circuit is not None:
                await asyncio.to_thread(circuit.abort,permit)
            raise
        return chosen,circuit,permit

    @staticmethod
    async def _report(circuit,permit,*,outcome,exc=None):
        if circuit is None:
            return
        if outcome == 'completed':
            await asyncio.to_thread(circuit.report,permit,healthy=True)
        elif exc is not None and not isinstance(exc,(asyncio.CancelledError,RunCancelled)):
            failure = classify_model_failure(exc)
            await asyncio.to_thread(circuit.report,permit,healthy=False,
                                    failure=failure.kind in {'transport','provider_unavailable','rate_limited'})
        else:
            await asyncio.to_thread(circuit.abort,permit)

    async def request(self, messages, model_settings, model_request_parameters):
        for retry in range(2):
            attempt_id = str(uuid4())
            with model_request_scope(self.trace_provider, stream=False, attempt_id=attempt_id) as span:
                attempt_settings,circuit,permit = await self._admit(
                    attempt_id,messages,model_settings,model_request_parameters)
                try:
                    response = await self.wrapped.request(messages, attempt_settings, model_request_parameters)
                except BaseException as exc:
                    cancelled = isinstance(exc,(asyncio.CancelledError,RunCancelled))
                    await self._finish(attempt_id, 'cancelled' if cancelled else 'error')
                    await self._report(circuit,permit,outcome='error',exc=exc)
                    finish_model_request(span, 'cancelled' if cancelled else 'incomplete')
                    if retry == 0 and classify_model_failure(exc).delivery == 'not_sent':
                        continue
                    raise
                outcome = _outcome(response.state)
                await self._finish(attempt_id,outcome,response)
                await self._report(circuit,permit,outcome=outcome)
                finish_model_request(span, outcome)
                return response

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        for retry in range(2):
            attempt_id = str(uuid4())
            with model_request_scope(self.trace_provider, stream=True, attempt_id=attempt_id) as span:
                attempt_settings,circuit,permit = await self._admit(
                    attempt_id,messages,model_settings,model_request_parameters)
                response = None
                entered = False
                try:
                    async with self.wrapped.request_stream(messages, attempt_settings,
                                                          model_request_parameters, run_context) as response:
                        entered = True
                        yield response
                        outcome = _outcome(response.get().state)
                except BaseException as exc:
                    cancelled = isinstance(exc,(asyncio.CancelledError,RunCancelled))
                    await self._finish(attempt_id, 'cancelled' if cancelled else 'error',response)
                    await self._report(circuit,permit,outcome='error',exc=exc)
                    finish_model_request(span, 'cancelled' if cancelled else 'incomplete')
                    if not entered and retry == 0 and classify_model_failure(exc).delivery == 'not_sent':
                        continue
                    raise
                # Keep scope active through provider context teardown; teardown errors
                # must fail this request rather than stamp a prematurely completed span.
                await self._finish(attempt_id,outcome,response)
                await self._report(circuit,permit,outcome=outcome)
                finish_model_request(span, outcome)
                return


def observe_model(model, *, provider, meter=None):
    if isinstance(model, ObservedModel):
        if model.trace_provider != provider:
            raise ValueError('Model trace provider mismatch')
        if meter is None or model.meter is meter:
            return model
        return ObservedModel(model.wrapped,provider=provider,meter=meter)
    return ObservedModel(model, provider=provider,meter=meter)
