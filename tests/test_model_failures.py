"""No network or supplier credentials are used in these classification tests."""

import httpx
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from app.model_failures import ModelFailure, classify_model_failure


@pytest.mark.parametrize(('status', 'expected'), [
    (400, ModelFailure('request_rejected', False, delivery='response_received')),
    (401, ModelFailure('authentication', False, True, 'response_received')),
    (402, ModelFailure('billing', False, True, 'response_received')),
    (403, ModelFailure('authentication', False, True, 'response_received')),
    (408, ModelFailure('provider_unavailable', True, delivery='response_received')),
    (413, ModelFailure('request_rejected', False, delivery='response_received')),
    (429, ModelFailure('rate_limited', True, delivery='response_received')),
    (500, ModelFailure('provider_unavailable', True, delivery='response_received')),
    (503, ModelFailure('provider_unavailable', True, delivery='response_received')),
    (599, ModelFailure('provider_unavailable', True, delivery='response_received')),
])
def test_http_failure_categories_ignore_sensitive_supplier_body(status, expected):
    error = ModelHTTPError(status, 'synthetic-model', body={'message': 'DO_NOT_LOG_SECRET'})
    result = classify_model_failure(error)
    assert result == expected
    assert 'DO_NOT_LOG_SECRET' not in repr(result)


def test_transport_and_unknown_failures_are_distinct():
    request = httpx.Request('POST', 'https://synthetic.invalid/v1')
    assert classify_model_failure(httpx.ConnectError('DO_NOT_LOG_SECRET', request=request)) == ModelFailure('transport', True, delivery='not_sent')
    assert classify_model_failure(httpx.ConnectTimeout('DO_NOT_LOG_SECRET', request=request)) == ModelFailure('transport', True, delivery='not_sent')
    assert classify_model_failure(ModelAPIError('synthetic-model', 'DO_NOT_LOG_SECRET')) == ModelFailure('transport', True)
    assert classify_model_failure(httpx.ReadTimeout('DO_NOT_LOG_SECRET', request=request)) == ModelFailure('transport', True)
    assert classify_model_failure(httpx.WriteTimeout('DO_NOT_LOG_SECRET', request=request)) == ModelFailure('transport', True)
    assert classify_model_failure(httpx.PoolTimeout('DO_NOT_LOG_SECRET', request=request)) == ModelFailure('transport', True, delivery='not_sent')
    assert classify_model_failure(RuntimeError('DO_NOT_LOG_SECRET')) == ModelFailure('unknown', False)


def test_retryable_is_not_automatic_replay_permission():
    assert classify_model_failure(ModelHTTPError(429, 'synthetic-model')).retryable
    assert not classify_model_failure(ModelHTTPError(401, 'synthetic-model')).retryable


def test_wrapped_connect_cause_is_not_sent_but_wrapped_read_timeout_is_unknown():
    request = httpx.Request('POST', 'https://synthetic.invalid/v1')
    connect = httpx.ConnectError('DO_NOT_LOG_SECRET', request=request)
    wrapped = ModelAPIError('synthetic-model', 'DO_NOT_LOG_SECRET')
    wrapped.__cause__ = connect
    assert classify_model_failure(wrapped).delivery == 'not_sent'
    read = ModelAPIError('synthetic-model', 'DO_NOT_LOG_SECRET')
    read.__cause__ = httpx.ReadTimeout('DO_NOT_LOG_SECRET', request=request)
    assert classify_model_failure(read).delivery == 'unknown'
