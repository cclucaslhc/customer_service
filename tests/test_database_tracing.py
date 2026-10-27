"""Database spans contain timing/correlation only, never SQL or parameters."""
import json
from uuid import uuid4

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest
import sqlalchemy as sa

from app.tracing import SafeTracing, install_database_tracing


def test_database_spans_are_parented_content_free_and_idempotent():
    engine = sa.create_engine('sqlite+pysqlite:///:memory:', hide_parameters=True)
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    install_database_tracing(engine)
    install_database_tracing(engine)
    request_id = str(uuid4())
    try:
        with engine.connect() as connection:
            connection.execute(sa.text('SELECT :secret_value'), {'secret_value': 'SECRET_BEFORE'})
            with tracing.scope('http.request', root=True, request_id=request_id):
                assert connection.execute(sa.text('SELECT :secret_value'),
                                          {'secret_value': 'SECRET_PARAMETER'}).scalar_one() == 'SECRET_PARAMETER'
                with pytest.raises(sa.exc.DBAPIError):
                    connection.execute(sa.text('SELECT SECRET_SQL_COLUMN FROM missing_table'))
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        parents = [span for span in spans if span.name == 'http.request']
        queries = [span for span in spans if span.name == 'db.query']
        assert len(parents) == 1 and len(queries) == 2
        assert all(span.parent.span_id == parents[0].context.span_id for span in queries)
        assert {span.attributes['outcome'] for span in queries} == {'completed', 'error'}
        assert all(set(span.attributes) <= {'request_id', 'outcome'} for span in queries)
        assert all(not span.events and not span.links and not span.status.description for span in queries)
        assert 'SECRET_' not in json.dumps([dict(span.attributes) for span in spans])
        assert tracing.snapshot()['rejected'] == 0
    finally:
        tracing.close()
        engine.dispose()
