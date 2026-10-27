"""Explicit loopback OTLP transport acceptance, with no production collector."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.otlp_export import PrivateOtlpTracing, _INHERITED_CONFIG


@pytest.fixture
def clean_otel_environment(monkeypatch):
    for name in _INHERITED_CONFIG:
        monkeypatch.delenv(name, raising=False)


def test_actual_otlp_http_delivery_is_content_free(clean_otel_environment):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == '/v1/traces'
            assert self.headers['x-customer-service-trace'] == 'explicit'
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    server = HTTPServer(('127.0.0.1', 0), Collector)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tracing = PrivateOtlpTracing(f'http://127.0.0.1:{server.server_port}/v1/traces')
    try:
        request_id = str(uuid4())
        with tracing.scope('http.request', root=True, request_id=request_id, method='GET'):
            pass
        assert tracing.flush(3)
        assert tracing.snapshot()['exported'] == 1
        assert len(received) == 1
        payload = ExportTraceServiceRequest.FromString(received[0])
        resource = payload.resource_spans[0]
        assert [(item.key, item.value.string_value) for item in resource.resource.attributes] == [
            ('service.name', 'customer-service')]
        span = resource.scope_spans[0].spans[0]
        assert span.name == 'http.request'
        assert {item.key for item in span.attributes} == {'request_id', 'http.request.method'}
        assert request_id.encode() in received[0]
        assert b'SECRET' not in received[0]
    finally:
        tracing.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not tracing.snapshot()['worker_alive']


@pytest.mark.parametrize('endpoint', [
    'http://example.com:4318/v1/traces', 'http://localhost:4318/v1/traces',
    'http://127.0.0.1:4318/other', 'http://127.0.0.1:4318/v1/traces?token=x',
    'http://user:password@127.0.0.1:4318/v1/traces',
    'https://example.com/v1/traces', 'file:///v1/traces', 'http://127.0.0.1:0/v1/traces',
])
def test_rejects_implicit_or_unsafe_endpoint(endpoint, clean_otel_environment):
    with pytest.raises(ValueError):
        PrivateOtlpTracing(endpoint)


def test_rejects_inherited_telemetry_credentials(monkeypatch, clean_otel_environment):
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_HEADERS', 'Authorization=SECRET_TOKEN')
    with pytest.raises(ValueError, match='Implicit OTLP') as error:
        PrivateOtlpTracing('http://127.0.0.1:4318/v1/traces')
    assert 'SECRET_TOKEN' not in str(error.value)
