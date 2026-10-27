"""Private object spans expose fixed operation names, not storage identifiers."""
import json
import uuid

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest

from app.object_store import ObjectStoreError
from app.tracing import SafeTracing, traced_object_operation
from test_object_store import Client, store


def test_object_tracing_covers_read_write_verification_and_failure():
    client = Client()
    selected = store(client)
    tenant = str(uuid.uuid4())
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    request_id = str(uuid.uuid4())
    try:
        selected.check_ready()  # No active trace: no Span.
        with tracing.scope('http.request', root=True, request_id=request_id):
            reference = selected.put(tenant, b'SECRET_OBJECT_CONTENT', kind='knowledge')
            assert selected.get(tenant, reference) == b'SECRET_OBJECT_CONTENT'
            client.versioning = 'Suspended'
            with pytest.raises(ObjectStoreError):
                selected.put(tenant, b'SECRET_FAILED_CONTENT', kind='knowledge')
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        roots = [span for span in spans if span.name == 'http.request']
        objects = [span for span in spans if span.name == 'object.request']
        assert len(roots) == 1 and len(objects) == 6
        assert {span.attributes['object.operation'] for span in objects} == {'put', 'get', 'check_ready'}
        assert {span.attributes['outcome'] for span in objects} == {'completed', 'error'}
        assert all(set(span.attributes) == {'request_id', 'object.operation', 'outcome'} for span in objects)
        assert all(span.context.trace_id == roots[0].context.trace_id for span in objects)
        assert all(not span.events and not span.links and not span.status.description for span in objects)
        assert tracing.snapshot()['rejected'] == 0
        assert 'SECRET_' not in json.dumps([dict(span.attributes) for span in spans])
    finally:
        tracing.close()


def test_object_trace_rejects_dynamic_operation_name():
    with pytest.raises(ValueError):
        traced_object_operation('tenants/SECRET_OBJECT_KEY')
