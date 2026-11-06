"""Synthetic registered span hierarchy through the actual application exporter."""
import json
import argparse
import time
import uuid
import requests
from app.otlp_export import PrivateOtlpTracing


def main(expect_outage=False,privacy_probe=False):
    deadline=time.monotonic()+30
    session=requests.Session()
    session.trust_env=False
    while not expect_outage:
        try:
            if session.get('http://127.0.0.1:13133',timeout=1).status_code==200: break
        except requests.RequestException: pass
        if time.monotonic()>deadline: raise RuntimeError('Collector readiness failed')
        time.sleep(.2)
    session.close()
    request_id,run_id,attempt_id=(str(uuid.uuid4()) for _ in range(3))
    if privacy_probe:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        payload=ExportTraceServiceRequest()
        resource=payload.resource_spans.add()
        resource.resource.attributes.add(key='synthetic.extra').value.string_value='synthetic-collector-private-sentinel'
        scope=resource.scope_spans.add()
        scope.scope.name='customer_service.safe'
        trace_bytes=uuid.uuid4().bytes
        span=scope.spans.add(name='db.query',trace_id=trace_bytes,span_id=uuid.uuid4().bytes[:8],
            start_time_unix_nano=time.time_ns(),end_time_unix_nano=time.time_ns()+1)
        for key,value in (('request_id',request_id),('run_id',run_id),
                          ('db.statement','synthetic-collector-private-sentinel')):
            span.attributes.add(key=key).value.string_value=value
        span.status.message='synthetic-collector-private-sentinel'
        span.events.add(name='synthetic-collector-private-sentinel',time_unix_nano=time.time_ns())
        scope.spans.add(name='synthetic-collector-private-sentinel',trace_id=trace_bytes,
            span_id=uuid.uuid4().bytes[:8],start_time_unix_nano=time.time_ns(),end_time_unix_nano=time.time_ns()+1)
        session=requests.Session()
        session.trust_env=False
        try:
            response=session.post('http://127.0.0.1:4318/v1/traces',data=payload.SerializeToString(),
                headers={'Content-Type':'application/x-protobuf'},timeout=3)
            assert response.status_code==200
        finally: session.close()
        print(json.dumps({'trace_id':trace_bytes.hex(),'expected_spans':1}))
        return
    tracing=PrivateOtlpTracing('http://127.0.0.1:4318/v1/traces')
    try:
        with tracing.scope('http.request',root=True,request_id=request_id,run_id=run_id,method='POST') as root:
            trace_id=f'{root.get_span_context().trace_id:032x}'
            with tracing.scope('agent.run'):
                with tracing.scope('model.request',attempt_id=attempt_id,model_provider='synthetic'):
                    pass
        assert tracing.flush(5)
        counts=tracing.snapshot()
        if expect_outage:
            assert counts['failed']>=1 and counts['exported']==0
        else:
            assert counts['exported']==3 and counts['failed']==0
        print(json.dumps({'trace_id':trace_id,'request_id':request_id,'run_id':run_id,
            'attempt_id':attempt_id,'expected_spans':3,'outage_observed':expect_outage,
            'exported':counts['exported'],'failed':counts['failed']}))
    finally: tracing.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-outage',action='store_true')
    parser.add_argument('--privacy-probe',action='store_true')
    args=parser.parse_args()
    main(args.expect_outage,args.privacy_probe)
