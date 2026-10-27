"""Classify provider failures without reading or logging response bodies."""

from dataclasses import dataclass

import httpx
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError


@dataclass(frozen=True)
class ModelFailure:
    kind: str
    retryable: bool
    configuration_action: bool = False
    # This is transport evidence, not authorization to replay an Agent run.
    # A previous tool side effect and a supplier charge still need separate
    # checks even when the current HTTP request was not sent.
    delivery: str = 'unknown'


_UNKNOWN = ModelFailure('unknown', False)


def classify_model_failure(exc: BaseException) -> ModelFailure:
    """Return a fixed category; never use supplier messages as public details.

    This is classification only. Callers must not infer permission to replay an
    Agent run or a tool side effect from ``retryable``.
    """
    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        if status == 429:
            return ModelFailure('rate_limited', True, delivery='response_received')
        if status == 408 or 500 <= status <= 599:
            return ModelFailure('provider_unavailable', True, delivery='response_received')
        if status in {401, 403}:
            return ModelFailure('authentication', False, True, 'response_received')
        if status == 402:
            return ModelFailure('billing', False, True, 'response_received')
        if 400 <= status <= 499:
            return ModelFailure('request_rejected', False, delivery='response_received')
        return _UNKNOWN
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return ModelFailure('transport', True, delivery='not_sent')
    if isinstance(exc, ModelAPIError):
        # OpenAI-compatible SDKs wrap the actual connect failure. Inspect only
        # typed causes, never exception text or provider payloads.
        cause = exc.__cause__
        seen = {id(exc)}
        for _ in range(4):
            if cause is None or id(cause) in seen:
                break
            seen.add(id(cause))
            if isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                return ModelFailure('transport', True, delivery='not_sent')
            cause = cause.__cause__
        return ModelFailure('transport', True)
    if isinstance(exc, httpx.TransportError):
        return ModelFailure('transport', True)
    return _UNKNOWN
