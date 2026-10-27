import asyncio
import json
import logging
import io
import os
from pathlib import Path
import socket
import subprocess
from time import monotonic

from fastapi.testclient import TestClient
import httpx
import pytest

from app.http_observability import HttpTelemetry
from app.private_metrics import MetricsApplication, MetricsPolicy, private_metrics_listener


TOKEN = b'synthetic-metrics-credential-not-for-production-0123456789'
AUTH = {'Authorization': 'Bearer ' + TOKEN.decode()}


def test_policy_is_explicit_validated_and_not_repr():
    assert TOKEN.decode() not in repr(MetricsPolicy(token=TOKEN))
    for token in (b'', b'weak', TOKEN + b'\n', 'not-bytes', b'a' * 257):
        with pytest.raises(ValueError, match='credential'):
            MetricsPolicy(token=token)
    for port in (-1, 65536, True, '9000'):
        with pytest.raises(ValueError, match='port'):
            MetricsPolicy(token=TOKEN, port=port)
    for timeout in (True, 0, 6, float('nan'), float('inf'), '1'):
        with pytest.raises(ValueError, match='timeout'):
            MetricsPolicy(token=TOKEN, export_timeout=timeout)
    for size in (True, 1023, 16 * 1024 * 1024 + 1, '4096'):
        with pytest.raises(ValueError, match='size'):
            MetricsPolicy(token=TOKEN, max_export_bytes=size)


@pytest.mark.asyncio
async def test_secondary_listener_never_reconfigures_parent_uvicorn_logs():
    loggers = [logging.getLogger(name) for name in ('uvicorn.access', 'uvicorn.error', 'uvicorn.asgi')]
    old = [(logger.handlers[:], logger.level, logger.propagate) for logger in loggers]
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    try:
        for logger in loggers:
            logger.handlers = [handler]
            logger.setLevel(logging.INFO)
            logger.propagate = False
        async with private_metrics_listener(HttpTelemetry(), MetricsPolicy(token=TOKEN)) as port:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f'http://127.0.0.1:{port}/SECRET_METRICS_PATH', headers=AUTH)
                assert response.status_code == 404
            for logger in loggers:
                assert logger.handlers == [handler] and logger.level == logging.INFO
                assert logger.propagate is False
        assert 'SECRET_' not in output.getvalue() and TOKEN.decode() not in output.getvalue()
        for logger in loggers:
            assert logger.handlers == [handler] and logger.level == logging.INFO
    finally:
        for logger, (handlers, level, propagate) in zip(loggers, old):
            logger.handlers, logger.level, logger.propagate = handlers, level, propagate


@pytest.mark.asyncio
async def test_auth_and_request_boundary_fail_closed():
    telemetry = HttpTelemetry()
    app = MetricsApplication(telemetry, MetricsPolicy(token=TOKEN), port=9012)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 123)),
                                base_url='http://127.0.0.1:9012', trust_env=False) as client:
        assert (await client.get('/metrics')).status_code == 401
        assert (await client.get('/metrics', headers={'Authorization':'Bearer wrong'})).status_code == 401
        assert (await client.get('/metrics', headers=[*AUTH.items(), *AUTH.items()])).status_code == 401
        assert (await client.get('/metrics?token=' + TOKEN.decode(), headers=AUTH)).status_code == 404
        assert (await client.get('/health', headers=AUTH)).status_code == 404
        assert (await client.post('/metrics', headers=AUTH)).status_code == 405
        for headers in ({'Origin':'http://127.0.0.1:8000'}, {'Referer':'http://evil.invalid'},
                        {'Sec-Fetch-Site':'same-origin'}, {'Host':'evil.invalid'}):
            response = await client.get('/metrics', headers={**AUTH, **headers})
            assert response.status_code == 403
            assert TOKEN.decode() not in response.text
        response = await client.get('/metrics', headers=AUTH)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'access-control-allow-origin' not in response.headers
        assert TOKEN.decode() not in response.text and '\r' not in response.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('192.0.2.1', 123)),
                                base_url='http://127.0.0.1:9012') as client:
        assert (await client.get('/metrics', headers={**AUTH, 'X-Forwarded-For':'127.0.0.1'})).status_code == 403
    assert telemetry.snapshot()['series'] == []  # Scrapes cannot inflate business traffic.


@pytest.mark.asyncio
async def test_export_failure_returns_no_partial_or_raw_data():
    class Failed:
        def prometheus(self, **kwargs):
            raise RuntimeError('SECRET_EXCEPTION_MARKER')
    app = MetricsApplication(Failed(), MetricsPolicy(token=TOKEN), port=9012)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9012') as client:
        response = await client.get('/metrics', headers=AUTH)
        assert response.status_code == 503
        assert 'SECRET_' not in response.text


@pytest.mark.asyncio
async def test_websocket_is_not_an_alternate_scrape_channel():
    app = MetricsApplication(HttpTelemetry(), MetricsPolicy(token=TOKEN), port=9012)
    messages = []
    async def send(message):
        messages.append(message)
    await app({'type': 'websocket'}, None, send)
    assert messages == [{'type': 'websocket.close', 'code': 1008}]


@pytest.mark.asyncio
async def test_actual_loopback_scrape_and_listener_shutdown():
    telemetry = HttpTelemetry()
    telemetry.record(route='/items/{item_id}', method='GET', status=200, outcome='completed',
                     stream=False, duration=.1, request_id='SECRET_REQUEST_ID')
    async with private_metrics_listener(telemetry, MetricsPolicy(token=TOKEN)) as port:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
            assert (await client.get('/metrics')).status_code == 401
            response = await client.get('/metrics', headers=AUTH)
            assert response.status_code == 200
            assert 'customer_service_http_requests_total' in response.text
            assert 'SECRET_' not in response.text and TOKEN.decode() not in response.text
            # No forwarded-host trust on the actual listener.
            assert (await client.get('/metrics', headers={**AUTH, 'Host':'evil.invalid',
                'X-Forwarded-Host':f'127.0.0.1:{port}'})).status_code == 403
    async with httpx.AsyncClient(trust_env=False, timeout=.5) as client:
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            await client.get(f'http://127.0.0.1:{port}/metrics', headers=AUTH)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as released:
        released.bind(('127.0.0.1', port))  # OS binding actually released, not just observation timeout.


@pytest.mark.asyncio
async def test_actual_scrape_parses_with_promtool():
    configured = os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('Explicit real promtool required')
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    telemetry = HttpTelemetry()
    telemetry.record(route='/stream', method='GET', status=200, outcome='error',
                     stream=True, duration=.02, request_id='synthetic')
    async with private_metrics_listener(telemetry, MetricsPolicy(token=TOKEN)) as port:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f'http://127.0.0.1:{port}/metrics', headers=AUTH)
            assert response.status_code == 200
    result = subprocess.run([str(binary), 'check', 'metrics'], input=response.content,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    example = Path(__file__).resolve().parents[1] / 'ops/prometheus/private-http.example.yml'
    validation = subprocess.run([str(binary), 'check', 'config', '--syntax-only', str(example)],
                                capture_output=True, timeout=30)
    assert validation.returncode == 0, validation.stderr.decode(errors='replace')


@pytest.mark.asyncio
async def test_bind_collision_does_not_steal_or_leak_listener():
    owned = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        owned.bind(('127.0.0.1', 0))
        owned.listen(1)
        port = owned.getsockname()[1]
        with pytest.raises(OSError):
            async with private_metrics_listener(HttpTelemetry(), MetricsPolicy(token=TOKEN, port=port)):
                pytest.fail('Occupied port must not start')
        assert owned.getsockname()[1] == port
    finally:
        owned.close()


def test_main_app_opt_in_lifecycle_and_public_separation(tmp_path, monkeypatch):
    import app.database as database
    from app.main import create_app
    from app.security import SecretStore
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'synthetic.db')
    monkeypatch.setattr(database, 'ensure_directories', lambda: None)
    paths = {key: tmp_path / key for key in ('knowledge_dir', 'asset_dir', 'legacy_upload_dir')}
    telemetry = HttpTelemetry()
    app = create_app(secret_store=SecretStore(path=tmp_path/'synthetic.key'), knowledge_paths=paths,
                     http_telemetry=telemetry, metrics_policy=MetricsPolicy(token=TOKEN))
    assert app.state.private_metrics_port is None
    with TestClient(app) as client:
        port = app.state.private_metrics_port
        assert isinstance(port, int) and port > 0
        assert client.get('/health/live').status_code == 200
        # SPA may return HTML, but public routing never exposes telemetry.
        public = client.get('/metrics', headers=AUTH)
        assert 'customer_service_http_requests_total' not in public.text
        with httpx.Client(trust_env=False, timeout=2) as collector:
            response = collector.get(f'http://127.0.0.1:{port}/metrics', headers=AUTH)
            assert response.status_code == 200 and '/health/live' in response.text
    assert app.state.private_metrics_port is None
    with httpx.Client(trust_env=False, timeout=.5) as collector:
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            collector.get(f'http://127.0.0.1:{port}/metrics', headers=AUTH)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as released:
        released.bind(('127.0.0.1', port))


@pytest.mark.asyncio
async def test_actual_prometheus_scrapes_authorized_target_and_rejects_wrong_credential(tmp_path):
    configured = os.environ.get('TEST_PROMETHEUS_PATH')
    if not configured:
        pytest.skip('Explicit real isolated Prometheus required')
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as allocation:
        allocation.bind(('127.0.0.1', 0))
        prometheus_port = allocation.getsockname()[1]
    credentials = tmp_path / 'synthetic-metrics-token'
    credentials.write_bytes(TOKEN + b'\n')
    wrong = tmp_path / 'synthetic-wrong-token'
    wrong.write_bytes(b'synthetic-wrong-metrics-credential-01234567890123456789\n')
    telemetry = HttpTelemetry()
    telemetry.record(route='/items/{item_id}', method='GET', status=200,
                     outcome='completed', stream=False, duration=.1, request_id='SECRET_ID')
    process = None
    async with private_metrics_listener(telemetry, MetricsPolicy(token=TOKEN)) as port:
        config = tmp_path / 'synthetic-prometheus.yml'
        config.write_text('global:\n  scrape_interval: 1s\n  scrape_timeout: 1s\nscrape_configs:\n' +
            ''.join(f'  - job_name: {job}\n    authorization:\n      type: Bearer\n'
                    f'      credentials_file: {json.dumps(credential.as_posix())}\n'
                    f'    static_configs:\n      - targets: ["127.0.0.1:{port}"]\n'
                    for job, credential in [('customer-service-private', credentials),
                                            ('synthetic-wrong-credential', wrong)]), encoding='utf-8')
        try:
            process = subprocess.Popen([str(binary), '--config.file=' + str(config),
                '--web.listen-address=127.0.0.1:' + str(prometheus_port),
                '--storage.tsdb.path=' + str(tmp_path / 'synthetic-tsdb'),
                '--storage.tsdb.retention.time=1h', '--log.level=error'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            queries = {'up{job="customer-service-private"}': '1',
                       'up{job="synthetic-wrong-credential"}': '0',
                       'customer_service_http_requests_total{job="customer-service-private"}': '1'}
            deadline = monotonic() + 25
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                while True:
                    assert process.poll() is None, 'Owned Prometheus exited before scrape acceptance'
                    observed = {}
                    try:
                        for query in queries:
                            response = await client.get(f'http://127.0.0.1:{prometheus_port}/api/v1/query',
                                                        params={'query': query})
                            if response.status_code == 200:
                                rows = response.json().get('data', {}).get('result', [])
                                if len(rows) == 1:
                                    observed[query] = rows[0]['value'][1]
                                    assert TOKEN.decode() not in response.text and 'SECRET_ID' not in response.text
                    except httpx.TransportError:
                        pass  # Same confirmed-live handle; never restart on observation timeout.
                    if observed == queries:
                        break
                    assert monotonic() < deadline, 'Actual Prometheus did not prove all expected scrape results'
                    await asyncio.sleep(.2)
        finally:
            if process is not None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
                assert process.poll() is not None
