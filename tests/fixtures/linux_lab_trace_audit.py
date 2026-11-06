"""Root-only exact-run audit of synthetic lab Collector records; no payload output."""
import argparse
import json
import os
from pathlib import Path
import socket
import time
import uuid


def main(run_id):
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file() or os.geteuid()!=0:
        raise RuntimeError('Owned disposable node2 and explicit root required')
    run_id=str(uuid.UUID(run_id))
    path=Path('/var/lib/cs-s2-otel/traces.json')
    deadline=time.monotonic()+20
    while True:
        spans=[]
        if path.stat().st_size>100*1024*1024: raise RuntimeError('Bounded trace audit exceeded')
        for line in path.read_text().splitlines():
            try: item=json.loads(line)
            except json.JSONDecodeError: continue
            for resource in item.get('resourceSpans',[]):
                for scope in resource.get('scopeSpans',[]):
                    spans.extend(scope.get('spans',[]))
        def attrs(span): return {a['key']:a['value'] for a in span.get('attributes',[])}
        workers=[s for s in spans if s['name']=='worker.chat' and attrs(s).get('run_id',{}).get('stringValue')==run_id]
        if workers:
            worker=workers[-1]
            selected=[s for s in spans if s['traceId']==worker['traceId']]
            names={s['name'] for s in selected}
            if {'http.request','worker.chat','agent.run','model.request','db.query'}<=names: break
        if time.monotonic()>deadline: raise RuntimeError('Expected exact-run trace hierarchy missing')
        time.sleep(.2)
    root=next(s for s in selected if s['spanId']==worker['parentSpanId'])
    assert root['name']=='http.request'
    request_id=attrs(root)['request_id']['stringValue']
    assert all(attrs(s).get('request_id',{}).get('stringValue')==request_id for s in selected)
    models=[s for s in selected if s['name']=='model.request']
    assert len(models)==1
    uuid.UUID(attrs(models[0])['attempt_id']['stringValue'])
    assert all(not s.get('events') and not s.get('links') for s in selected)
    mode=path.parent.stat().st_mode&0o777
    assert mode==0o700
    print(json.dumps({'run_id':run_id,'trace_id':worker['traceId'],'span_names':sorted(names),
        'span_count':len(selected),'worker_parent_is_api':True,'request_id_shared':True,
        'model_attempts':1,'state_directory_mode':oct(mode)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    main(parser.parse_args().run_id)
