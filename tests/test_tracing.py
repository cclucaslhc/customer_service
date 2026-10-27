import asyncio
import json
from types import SimpleNamespace
from threading import Event
import uuid

from fastapi import Request
from fastapi.testclient import TestClient
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.http_observability import HttpTelemetry, ObservedFastAPI
from app.knowledge_worker import KnowledgeWorker
from app.tracing import SafeTracing, capture_carrier, validate_carrier, agent_scope, operation_scope


def test_knowledge_worker_root_is_opt_in_content_free_and_marks_errors(monkeypatch):
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    job_id = str(uuid.uuid4())
    job = {'lease':SimpleNamespace(job_id=job_id), 'credential':'SECRET_CREDENTIAL',
           'trace_context':None}
    worker = KnowledgeWorker.__new__(KnowledgeWorker)
    worker.queue = SimpleNamespace(claim_next=lambda: job)
    worker.tracing = tracing
    try:
        def completed(claimed):
            with operation_scope('object.request', object_operation='get'):
                return 'completed'
        monkeypatch.setattr(worker, '_execute', completed)
        assert worker.run_once()
        def failed(claimed):
            raise RuntimeError('SECRET_PARSE_ERROR')
        monkeypatch.setattr(worker, '_execute', failed)
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())) as http_span:
            job['trace_context'] = capture_carrier()
        with pytest.raises(RuntimeError, match='SECRET_PARSE_ERROR'):
            worker.run_once()
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 4
        roots = [span for span in spans if span.name == 'worker.knowledge']
        child = next(span for span in spans if span.name == 'object.request')
        assert len(roots) == 2
        assert next(span for span in roots if span.attributes['outcome'] == 'completed').parent is None
        assert next(span for span in roots if span.attributes['outcome'] == 'error').parent.span_id == http_span.context.span_id
        assert child.parent.span_id == next(span for span in roots if span.attributes['outcome'] == 'completed').context.span_id
        assert all(dict(span.attributes).keys() <= {'job_id','outcome','object.operation','request_id'} for span in spans)
        assert {span.attributes['outcome'] for span in roots} == {'completed','error'}
        assert all(span.attributes['job_id'] == job_id for span in roots+[child])
        assert all(not span.events and not span.links and not span.status.description for span in spans)
        assert 'SECRET_' not in json.dumps([dict(span.attributes) for span in spans])
    finally:
        tracing.close()


def test_knowledge_worker_marks_committed_item_failure_as_error(monkeypatch):
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    worker = KnowledgeWorker.__new__(KnowledgeWorker)
    worker.queue = SimpleNamespace(claim_next=lambda:{'lease':SimpleNamespace(job_id=str(uuid.uuid4())),
        'trace_context':None})
    worker.tracing = tracing
    monkeypatch.setattr(worker, '_execute', lambda job:'error')
    try:
        assert worker.run_once()
        assert tracing.flush()
        span = exporter.get_finished_spans()[0]
        assert span.name == 'worker.knowledge'
        assert span.attributes['outcome'] == 'error'
        assert span.status.status_code == trace.StatusCode.ERROR
    finally:
        tracing.close()


def test_http_spans_log_correlation_untrusted_headers_and_no_content():
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    records = []
    app = ObservedFastAPI(tracing=tracing,telemetry=HttpTelemetry(sink=records.append))
    @app.post('/items/{item_id}')
    async def item(request: Request):
        await request.body()
        assert capture_carrier()['request_id'] == request.state.request_id
        return {'answer':'SECRET_ANSWER'}
    @app.get('/failure')
    async def failure():
        raise RuntimeError('SECRET_EXCEPTION')
    try:
        with TestClient(app,raise_server_exceptions=False) as client:
            response = client.post('/items/SECRET_PATH?query=SECRET_QUERY',content='SECRET_BODY',headers={
                'Authorization':'SECRET_AUTH','Cookie':'SECRET_COOKIE','baggage':'SECRET_BAGGAGE',
                'traceparent':'00-'+'a'*32+'-'+'b'*16+'-01'})
            assert response.status_code==200
            assert client.get('/failure').status_code==500
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        assert len(spans)==2 and all(span.parent is None for span in spans)
        first = next(span for span in spans if span.attributes['http.route']=='/items/{item_id}')
        assert first.attributes['request_id']==response.headers['X-Request-ID']
        assert first.context.trace_id != int('a'*32,16)
        assert any(row['trace_id']==f'{first.context.trace_id:032x}' for row in records)
        assert next(span for span in spans if span.attributes['http.route']=='/failure').status.status_code.name=='ERROR'
        assert 'SECRET_' not in json.dumps(records)
        assert 'SECRET_' not in json.dumps([dict(span.attributes) for span in spans])
        assert all(not span.events and not span.links and not span.status.description for span in spans)
        assert capture_carrier() is None
    finally:
        tracing.close()


@pytest.mark.asyncio
async def test_parent_carrier_agent_children_and_context_isolation():
    exporter = InMemorySpanExporter(); tracing = SafeTracing(exporter)
    try:
        async def exercise():
            request_id, run_id = str(uuid.uuid4()),str(uuid.uuid4())
            with tracing.scope('http.request',root=True,request_id=request_id):
                carrier = capture_carrier()
            with tracing.scope('worker.chat',root=True,carrier=carrier,run_id=run_id):
                async with agent_scope(run_id):
                    await asyncio.sleep(.01)
                    assert capture_carrier()['request_id']==request_id
            assert capture_carrier() is None
            return request_id
        ids = await asyncio.gather(*(exercise() for _ in range(8)))
        assert tracing.flush()
        spans=exporter.get_finished_spans()
        assert len(spans)==24
        for request_id in ids:
            group={span.name:span for span in spans if span.attributes['request_id']==request_id}
            assert group['worker.chat'].parent.span_id==group['http.request'].context.span_id
            assert group['agent.run'].parent.span_id==group['worker.chat'].context.span_id
            assert len({span.context.trace_id for span in group.values()})==1
    finally:
        tracing.close()


def test_invalid_carriers_are_rejected_without_raw_error_text():
    carrier={'request_id':str(uuid.uuid4()),'trace_id':'a'*32,'span_id':'b'*16,'sampled':True}
    assert validate_carrier(carrier)==carrier
    for bad in ({**carrier,'baggage':'SECRET'}, {**carrier,'trace_id':'0'*32},
                {**carrier,'request_id':'SECRET'}, {**carrier,'sampled':'SECRET'},
                {**carrier,'span_id':'C'*16}, [], {}):
        with pytest.raises(ValueError) as failure:
            validate_carrier(bad)
        assert 'SECRET' not in str(failure.value)


def test_blocked_export_does_not_block_span_end_and_queue_is_bounded():
    class Blocked(SpanExporter):
        started, release = Event(), Event()
        def export(self, spans):
            self.started.set(); assert self.release.wait(3)
            return SpanExportResult.SUCCESS
    exporter=Blocked(); tracing=SafeTracing(exporter,capacity=2)
    try:
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())):
            pass
        assert exporter.started.wait(1)
        for _ in range(20):
            with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())):
                pass
        assert tracing.snapshot()['queue_depth']==2
        assert tracing.snapshot()['dropped']==18
        assert not tracing.flush(.01)
        exporter.release.set()
        assert tracing.flush()
    finally:
        exporter.release.set(); tracing.close()
    assert not tracing.snapshot()['worker_alive']


def test_exporter_failure_is_counted_and_unsafe_attributes_are_not_exported():
    class Failed(SpanExporter):
        def export(self, spans):
            raise RuntimeError('SECRET_EXPORT_EXCEPTION')
    tracing=SafeTracing(Failed())
    try:
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())) as span:
            span.set_attribute('prompt','SECRET_PROMPT')
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())) as span:
            span.set_attribute('request_id','SECRET_ID')
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())):
            pass
        assert tracing.flush()
        assert tracing.snapshot()['rejected']==2 and tracing.snapshot()['failed']==1
        telemetry=HttpTelemetry(); telemetry.tracing=tracing
        assert 'customer_service_trace_spans_total{outcome="rejected"} 2' in telemetry.prometheus()
        assert 'SECRET' not in str(tracing.snapshot())
    finally:
        tracing.close()


def test_unsampled_parent_is_preserved_without_export_or_global_provider_mutation():
    global_provider=trace.get_tracer_provider()
    exporter=InMemorySpanExporter(); tracing=SafeTracing(exporter)
    carrier={'request_id':str(uuid.uuid4()),'trace_id':'a'*32,'span_id':'b'*16,'sampled':False}
    try:
        with tracing.scope('worker.chat',root=True,carrier=carrier,run_id=str(uuid.uuid4())):
            assert capture_carrier()['sampled'] is False
            assert capture_carrier()['trace_id']==carrier['trace_id']
        assert tracing.flush() and not exporter.get_finished_spans()
        assert trace.get_tracer_provider() is global_provider
        assert capture_carrier() is None
    finally:
        tracing.close()
