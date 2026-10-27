"""Opt-in, content-filtered OTLP/HTTP trace transport.

An operator supplies an exact collector endpoint. Nothing is inferred from the
process environment or from model/provider credentials.
"""

import ipaddress
import os
from urllib.parse import urlsplit

import requests
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from app.tracing import SafeTracing


_INHERITED_CONFIG = frozenset((
    'OTEL_EXPORTER_OTLP_ENDPOINT', 'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT',
    'OTEL_EXPORTER_OTLP_HEADERS', 'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
    'OTEL_EXPORTER_OTLP_CERTIFICATE', 'OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE',
    'OTEL_EXPORTER_OTLP_CLIENT_KEY', 'OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY',
    'OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE',
    'OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE',
    'OTEL_EXPORTER_OTLP_TIMEOUT', 'OTEL_EXPORTER_OTLP_TRACES_TIMEOUT',
    'OTEL_EXPORTER_OTLP_COMPRESSION', 'OTEL_EXPORTER_OTLP_TRACES_COMPRESSION',
    'OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER',
))


def _validate_endpoint(endpoint):
    if type(endpoint) is not str or len(endpoint) > 512 or any(ord(c) < 33 for c in endpoint):
        raise ValueError('Explicit OTLP collector endpoint required')
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid OTLP collector endpoint') from None
    if (not parsed.hostname or not port or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != '/v1/traces'):
        raise ValueError('Invalid OTLP collector endpoint')
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip = None
    if parsed.scheme == 'http':
        if ip is None or not ip.is_loopback:
            raise ValueError('Plain HTTP OTLP requires an IP loopback endpoint')
    elif parsed.scheme != 'https':
        raise ValueError('OTLP collector must use HTTPS or loopback HTTP')
    return endpoint


class PrivateOtlpTracing(SafeTracing):
    def __init__(self, endpoint, *, capacity=256):
        endpoint = _validate_endpoint(endpoint)
        if any(name in os.environ for name in _INHERITED_CONFIG):
            raise ValueError('Implicit OTLP environment configuration is not permitted')
        session = requests.Session()
        session.trust_env = False
        exporter = OTLPSpanExporter(
            endpoint=endpoint, certificate_file=True,
            headers={'x-customer-service-trace': 'explicit'}, timeout=1.0,
            compression=Compression.NoCompression, session=session)
        try:
            super().__init__(exporter, capacity=capacity)
        except BaseException:
            exporter.shutdown()
            raise
        self._owned_exporter = exporter

    def close(self):
        super().close()
        # A timed-out exporter may still own this session on its daemon thread.
        if not self.processor.thread.is_alive():
            self._owned_exporter.shutdown()
