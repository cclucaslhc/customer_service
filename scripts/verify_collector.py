"""Actual Collector pipeline and outage/recovery proof on isolated loopback."""
import json
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
COLLECTOR='otel/opentelemetry-collector-contrib@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b'


def runtime_user_args():
    # The private 0700 evidence directory belongs to the non-root test runner.
    # On POSIX use that same UID/GID rather than widening it or running as root.
    # Docker Desktop's Windows bind mounts do not use POSIX owner matching.
    if not hasattr(os,'getuid'):
        return []
    uid,gid=os.getuid(),os.getgid()
    if uid==0:
        raise ValueError('Run Collector acceptance as a non-root host user')
    return ['--user',f'{uid}:{gid}']


def docker(*args):
    result=subprocess.run(['docker',*map(str,args)],capture_output=True,text=True,timeout=60)
    if result.returncode: raise RuntimeError('Collector acceptance operation failed: '+str(args[0]))
    return result.stdout.strip()


def main(app_image):
    name='cs-s2-collector-'+uuid.uuid4().hex[:12]
    evidence=Path(tempfile.mkdtemp(prefix=name+'-'))
    started=False
    try:
        config=['--mount',f'type=bind,source={ROOT / "ops/otel/collector.yaml"},target=/etc/otelcol/config.yaml,readonly']
        docker('run','--rm','--network','none',*config,COLLECTOR,'validate','--config=/etc/otelcol/config.yaml')
        docker('run','-d','--name',name,'--network','none','--memory=256m','--pids-limit=64',
            '--cap-drop=ALL','--security-opt=no-new-privileges',*runtime_user_args(),*config,
            '--mount',f'type=bind,source={evidence},target=/var/lib/customer-service-otel',
            COLLECTOR,'--config=/etc/otelcol/config.yaml')
        started=True
        def probe(outage=False,privacy=False):
            raw=docker('run','--rm','--network','container:'+name,'--memory=256m','--pids-limit=64',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--env','PYTHONPATH=/opt/customer-service',
                '--mount',f'type=bind,source={ROOT / "tests/fixtures/collector_trace_probe.py"},target=/probe.py,readonly',
                '--mount',f'type=bind,source={ROOT / "app"},target=/opt/customer-service/app,readonly',
                app_image,'/opt/customer-service/.venv/bin/python','/probe.py',
                *(['--expect-outage'] if outage else []),*(['--privacy-probe'] if privacy else []))
            return json.loads(raw)
        def spans_for(proof):
            deadline=time.monotonic()+30
            while True:
                file=evidence/'traces.json'
                spans=[]
                if file.is_file():
                    for line in file.read_text(encoding='utf-8').splitlines():
                        try: batch=json.loads(line)
                        except json.JSONDecodeError: continue # Concurrent writer may not have completed its last line.
                        for resource in batch.get('resourceSpans',[]):
                            for scope in resource.get('scopeSpans',[]):
                                spans.extend(span for span in scope.get('spans',[]) if span['traceId']==proof['trace_id'])
                if len(spans)==proof['expected_spans']: return spans
                assert time.monotonic()<deadline,'Trace not persisted by real Collector'
                time.sleep(.2)
        def verify(proof):
            spans=spans_for(proof)
            by_name={span['name']:span for span in spans}
            assert set(by_name)=={'http.request','agent.run','model.request'}
            assert by_name['agent.run']['parentSpanId']==by_name['http.request']['spanId']
            assert by_name['model.request']['parentSpanId']==by_name['agent.run']['spanId']
            for span in spans:
                attrs={item['key']:item['value'] for item in span.get('attributes',[])}
                assert attrs['request_id']['stringValue']==proof['request_id']
                assert attrs['run_id']['stringValue']==proof['run_id']
                assert not span.get('events') and not span.get('links')
            assert any(item['key']=='attempt_id' and item['value']['stringValue']==proof['attempt_id']
                for item in by_name['model.request']['attributes'])
        first=probe()
        verify(first)
        privacy=probe(privacy=True)
        filtered=spans_for(privacy)
        assert [span['name'] for span in filtered]==['db.query']
        assert not filtered[0].get('events')
        assert 'synthetic-collector-private-sentinel' not in (evidence/'traces.json').read_text(encoding='utf-8')
        # Pause freezes the Collector in the same namespace without discarding
        # its network identity. Application exporter must time out, not report
        # these spans as exported. Always unpause in finally.
        docker('pause',name)
        try: outage=probe(True)
        finally: docker('unpause',name)
        recovered=probe()
        verify(recovered)
        result={'collector_config_validated':True,'persisted_trace_before':first['trace_id'],
            'persisted_trace_after':recovered['trace_id'],'span_parent_and_ids_verified':True,
            'unknown_attributes_status_events_and_span_name_removed':True,
            'paused_collector_export_failed':outage['failed'],'runtime_directory':str(evidence),
            'scope':'synthetic direct application scopes through real Collector; not deployed API/Worker transport'}
        (evidence/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)
    finally:
        if started: docker('rm','-f',name)
        # Keep only owned synthetic evidence; no global prune or broad deletion.


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-image',required=True,help='Explicit current application image')
    main(parser.parse_args().app_image)
