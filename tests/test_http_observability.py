import asyncio
import json
import os
import subprocess
from time import perf_counter
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.http_observability import HttpTelemetry, ObservedFastAPI, HttpObservationMiddleware, request_id_context


def test_content_free_logs_ids_errors_and_cardinality():
    records = []
    telemetry = HttpTelemetry(sink=records.append)
    app = ObservedFastAPI(telemetry=telemetry)

    @app.post('/items/{item_id}')
    async def item(request: Request, item_id: str):
        assert request_id_context.get() == request.state.request_id
        await request.body()
        return {'ok': True}

    @app.get('/failure')
    async def failure():
        raise RuntimeError('SECRET_EXCEPTION_MARKER')

    with TestClient(app, raise_server_exceptions=False) as client:
        ids = []
        for i in range(25):
            response = client.post(f'/items/SECRET_PATH_{i}?query=SECRET_QUERY',
                content='SECRET_BODY', headers={'Authorization': 'SECRET_AUTH',
                'Cookie': 'SECRET_COOKIE', 'X-Request-ID': 'SECRET_ID\r\nINJECTION'})
            assert response.status_code == 200
            ids.append(response.headers['X-Request-ID'])
            UUID(ids[-1])
        assert len(set(ids)) == 25
        response = client.get('/failure')
        assert response.status_code == 500
        assert response.headers['X-Request-ID'] == records[-1]['request_id']
        for i in range(25):
            client.get(f'/SECRET_MISSING_{i}')
        assert client.get('/metrics').status_code == 404
    assert request_id_context.get() is None
    assert 'SECRET_' not in json.dumps(records)
    assert 'SECRET_' not in telemetry.prometheus()
    snapshot = telemetry.snapshot()
    assert len(snapshot['series']) == 3
    assert sum(row['count'] for row in snapshot['series']) == 52
    failed = next(row for row in snapshot['series'] if row['route'] == '/failure')
    assert failed['outcome'] == 'error' and failed['status_class'] == '5xx'


def test_sse_completion_and_broken_sink_do_not_break_response():
    def broken(record):
        raise ValueError('SECRET_LOGGER_FAILURE')
    telemetry = HttpTelemetry(sink=broken)
    app = ObservedFastAPI(telemetry=telemetry)

    @app.get('/stream')
    async def stream():
        async def chunks():
            yield 'data: one\n\n'
            # Windows asyncio timers may wake before a small requested sleep;
            # enforce an actual high-resolution elapsed interval.
            started = perf_counter()
            while perf_counter() - started < .025:
                await asyncio.sleep(.005)
            yield 'data: two\n\n'
        return StreamingResponse(chunks(), media_type='text/event-stream')

    with TestClient(app) as client:
        response = client.get('/stream')
        assert response.text == 'data: one\n\ndata: two\n\n'
    snapshot = telemetry.snapshot()
    assert snapshot['dropped_logs'] == 1
    row = snapshot['series'][0]
    assert row['stream'] and row['outcome'] == 'completed'
    assert row['duration_sum'] >= .025
    assert row['bucket_counts'][-1] == row['count'] == 1
    output = telemetry.prometheus()
    assert 'customer_service_http_sse_connection_duration_seconds_count' in output
    assert 'customer_service_http_log_sink_failures_total 1' in output
    assert '\r' not in output


@pytest.mark.parametrize('mode', ['disconnect', 'cancel', 'incomplete', 'send_error'])
def test_terminal_boundaries_and_context_reset(mode):
    async def exercise():
        records = []
        telemetry = HttpTelemetry(sink=records.append)
        scope = {'type': 'http', 'method': 'SECRET_METHOD', 'path': '/SECRET_PATH', 'headers': []}
        async def receive():
            return {'type': 'http.disconnect'}
        async def send(message):
            if mode == 'send_error':
                raise OSError('SECRET_SEND_ERROR')
        async def app(scope, receive, send):
            assert request_id_context.get() == scope['state']['request_id']
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            if mode == 'disconnect':
                await receive()
            elif mode == 'cancel':
                raise asyncio.CancelledError()
        middleware = HttpObservationMiddleware(app, telemetry=telemetry, routes=[])
        if mode in ('cancel', 'send_error'):
            with pytest.raises(asyncio.CancelledError if mode == 'cancel' else OSError):
                await middleware(scope, receive, send)
        else:
            await middleware(scope, receive, send)
        assert request_id_context.get() is None
        assert len(records) == 1
        assert records[0]['outcome'] == {'disconnect':'disconnected', 'cancel':'cancelled',
                                       'incomplete':'incomplete', 'send_error':'error'}[mode]
        assert records[0]['route'] == '__unmatched__' and records[0]['method'] == 'OTHER'
        assert 'SECRET_' not in json.dumps(records)
    asyncio.run(exercise())


def test_prometheus_format_with_real_promtool():
    tool = os.environ.get('TEST_PROMTOOL_PATH')
    if not tool:
        pytest.skip('Explicit real promtool required')
    telemetry = HttpTelemetry()
    for stream in (False, True):
        telemetry.record(route='/items/{item_id}', method='GET', status=200,
                         outcome='completed', stream=stream, duration=.02, request_id='synthetic')
    result = subprocess.run([tool, 'check', 'metrics'], input=telemetry.prometheus().encode(),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def test_concurrent_context_isolation():
    async def exercise():
        ids = []
        telemetry = HttpTelemetry()
        async def app(scope, receive, send):
            own_id = request_id_context.get()
            await asyncio.sleep(.01)
            assert request_id_context.get() == own_id
            ids.append(own_id)
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b''})
        async def noop(*args):
            pass
        middleware = HttpObservationMiddleware(app, telemetry=telemetry, routes=[])
        await asyncio.gather(*(middleware({'type':'http','method':'GET'},noop,noop) for _ in range(20)))
        assert len(set(ids)) == 20 and request_id_context.get() is None
        assert telemetry.snapshot()['series'][0]['count'] == 20
    asyncio.run(exercise())
