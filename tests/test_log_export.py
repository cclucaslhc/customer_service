"""Bounded private HTTP log delivery; never log raw requests or writer errors."""
from datetime import datetime, timezone
import json
from threading import Event
from uuid import uuid4

from fastapi import Request
from fastapi.testclient import TestClient

from app.http_observability import HttpTelemetry, ObservedFastAPI
from app.log_export import BoundedJsonLogSink


def record(**changes):
    value = {'timestamp':datetime.now(timezone.utc).isoformat(),'level':'INFO',
             'service':'customer-service','event':'http.completed','request_id':str(uuid4()),
             'route':'/items/{item_id}','method':'GET','status':200,'outcome':'completed',
             'stream':False,'duration_ms':1.25}
    return {**value,**changes}


def test_bounded_queue_drops_without_blocking_and_closes_after_writer_recovers():
    entered,released = Event(),Event()
    written = []
    def writer(line):
        entered.set()
        released.wait(3)
        written.append(line)
    sink = BoundedJsonLogSink(writer,capacity=2)
    try:
        sink(record())
        assert entered.wait(1)
        sink(record())
        sink(record())
        sink(record())
        assert sink.snapshot()['dropped'] == 1
        assert sink.snapshot()['queue_depth'] == 2
        assert not sink.flush(.02)
        assert not sink.close(.02)
    finally:
        released.set()
        assert sink.flush(2)
        assert sink.close(2)
    assert len(written) == 3
    assert sink.snapshot()['written'] == 3
    assert not sink.snapshot()['worker_alive']


def test_allowlist_rejects_content_and_writer_errors_are_only_counted():
    lines = []
    sink = BoundedJsonLogSink(lines.append,max_bytes=512)
    try:
        sink(record(prompt='SECRET_PROMPT'))
        sink(record(route='/items/SECRET_REAL_ID?query=SECRET'))
        sink(record(request_id='SECRET_UUID'))
        sink(record(trace_id='0'*32))
        sink(record())
        assert sink.flush()
        assert sink.snapshot()['rejected'] == 4
        assert sink.snapshot()['written'] == 1
        assert 'SECRET_' not in ''.join(lines)
        assert set(json.loads(lines[0])) == set(record())
    finally:
        assert sink.close()

    def failing(_):
        raise RuntimeError('SECRET_WRITER_EXCEPTION')
    broken = BoundedJsonLogSink(failing)
    try:
        broken(record())
        assert broken.flush()
        assert broken.snapshot()['failed'] == 1
    finally:
        assert broken.close()


def test_http_records_are_private_bounded_and_report_low_cardinality_metrics():
    lines = []
    sink = BoundedJsonLogSink(lines.append)
    telemetry = HttpTelemetry(sink=sink)
    app = ObservedFastAPI(telemetry=telemetry)
    @app.post('/items/{item_id}')
    async def item(request: Request):
        await request.body()
        return {'ok':True}
    try:
        with TestClient(app) as client:
            result = client.post('/items/SECRET_PATH?query=SECRET_QUERY',content='SECRET_BODY',
                headers={'Authorization':'SECRET_KEY','Cookie':'SECRET_COOKIE'})
            assert result.status_code == 200
        assert sink.flush()
        assert len(lines) == 1
        assert json.loads(lines[0])['route'] == '/items/{item_id}'
        assert json.loads(lines[0])['request_id'] == result.headers['X-Request-ID']
        assert 'SECRET_' not in lines[0]
        metrics = telemetry.prometheus()
        assert 'customer_service_http_log_records_total{outcome="written"} 1' in metrics
        assert 'customer_service_http_log_queue_depth 0' in metrics
        assert 'SECRET_' not in metrics
    finally:
        assert sink.close()
