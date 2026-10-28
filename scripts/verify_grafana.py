"""Opt-in real isolated Grafana/Prometheus dashboard acceptance, no model calls."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import httpx

ROOT=Path(__file__).resolve().parents[1]
PROMETHEUS='prom/prometheus@sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2'


def docker(*args):
    result=subprocess.run(['docker',*map(str,args)],capture_output=True,text=True,timeout=60)
    if result.returncode:
        raise RuntimeError('Isolated Docker operation failed: '+str(args[0]))
    return result.stdout.strip()


def wait(client,path,timeout=60):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            response=client.get(path)
            if response.status_code==200: return response
        except httpx.TransportError: pass
        time.sleep(.5)
    raise RuntimeError('Isolated Grafana readiness timed out')


def main(app_image,grafana_image,keep=False):
    prefix='cs-s2-grafana-'+uuid.uuid4().hex[:12]
    folder=Path(tempfile.mkdtemp(prefix=prefix+'-'))
    names=[]
    network=False
    success=False
    try:
        token='synthetic-grafana-metrics-only-01234567890123456789'
        (folder/'metrics-token').write_text(token,encoding='utf-8')
        (folder/'prometheus.yml').write_text('global:\n  scrape_interval: 1s\n'
            'scrape_configs:\n  - job_name: customer-service-private\n'
            '    authorization:\n      credentials_file: /evidence/metrics-token\n'
            '    static_configs:\n      - targets: ["127.0.0.1:9107"]\n',encoding='utf-8')
        # Dedicated bridge; only Grafana is published, strictly to host loopback.
        # Docker's internal-network mode suppresses that published endpoint on
        # this host. This is isolation from other app networks, not an egress ACL.
        docker('network','create',prefix)
        network=True
        grafana=prefix+'-ui'
        docker('run','-d','--name',grafana,'--network',prefix,'--publish','127.0.0.1::3000',
            '--memory=512m','--pids-limit=128','--cap-drop=ALL','--security-opt=no-new-privileges',
            '--env','GF_SECURITY_ADMIN_USER=synthetic-admin',
            '--env','GF_SECURITY_ADMIN_PASSWORD=synthetic-grafana-only-password',
            '--env','GF_AUTH_ANONYMOUS_ENABLED=false','--env','GF_USERS_ALLOW_SIGN_UP=false',
            '--env','GOMAXPROCS=2',
            '--env','GF_ANALYTICS_REPORTING_ENABLED=false','--env','GF_ANALYTICS_CHECK_FOR_UPDATES=false',grafana_image)
        names.append(grafana)
        for name,image,arguments in (
            (prefix+'-prom',PROMETHEUS,['--config.file=/evidence/prometheus.yml','--web.listen-address=127.0.0.1:9090','--storage.tsdb.retention.time=1h']),
            (prefix+'-metrics',app_image,['/opt/customer-service/.venv/bin/python','/fixture.py']),
        ):
            mounts=(['--mount',f'type=bind,source={folder},target=/evidence,readonly'] if image==PROMETHEUS else
                ['--mount',f'type=bind,source={ROOT / "tests/fixtures/grafana_metrics.py"},target=/fixture.py,readonly',
                 '--mount',f'type=bind,source={ROOT / "app"},target=/opt/customer-service/app,readonly',
                 '--env','PYTHONPATH=/opt/customer-service'])
            docker('run','-d','--name',name,'--network','container:'+grafana,
                '--memory=512m','--pids-limit=128','--cap-drop=ALL','--security-opt=no-new-privileges',
                *mounts,image,*arguments)
            names.append(name)
        port=int(docker('port',grafana,'3000/tcp').rsplit(':',1)[1])
        url=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=url,auth=('synthetic-admin','synthetic-grafana-only-password'),trust_env=False,timeout=10) as client:
            wait(client,'/api/health')
            assert httpx.get(url+'/api/datasources',trust_env=False).status_code==401
            response=client.post('/api/datasources',json={'name':'Synthetic isolated Prometheus','uid':'cs-s2-prom',
                'type':'prometheus','url':'http://127.0.0.1:9090','access':'proxy','isDefault':True})
            assert response.status_code==200
            wait(client,'/api/datasources/uid/cs-s2-prom/health')
            dashboard=json.loads((ROOT/'ops/grafana/customer-service-s2.json').read_text(encoding='utf-8'))
            response=client.post('/api/dashboards/db',json={'dashboard':dashboard,'overwrite':False})
            assert response.status_code==200
            stored=client.get('/api/dashboards/uid/customer-service-s2')
            assert stored.status_code==200 and len(stored.json()['dashboard']['panels'])==6
            deadline=time.monotonic()+45
            while True:
                response=client.get('/api/datasources/proxy/uid/cs-s2-prom/api/v1/query',params={
                    'query':'up{job="customer-service-private"}'})
                rows=response.json().get('data',{}).get('result',[]) if response.status_code==200 else []
                if rows and rows[0]['value'][1]=='1': break
                assert time.monotonic()<deadline,'Real private target scrape did not become healthy'
                time.sleep(1)
            time.sleep(4)  # Multiple real samples needed for histogram rates.
            queries=[]
            for panel in dashboard['panels']:
                expression=panel['targets'][0]['expr'].replace('$__rate_interval','1m').replace('$aggregate_instance','127.0.0.1:9107')
                response=client.get('/api/datasources/proxy/uid/cs-s2-prom/api/v1/query',params={'query':expression})
                assert response.status_code==200 and response.json()['status']=='success',panel['title']
                rows=response.json()['data']['result']
                if panel['id'] in (1,2): assert rows,panel['title']
                if panel['id']==3: assert not rows,'Absent DB evidence must not become healthy zero'
                queries.append({'panel':panel['id'],'series':len(rows)})
            result={'url':url,'dashboard':'/d/customer-service-s2','containers':names,'network':prefix,
                'image_ids':{name:docker('inspect','--format={{.Image}}',name) for name in names},
                'runtime_directory':str(folder),'queries':queries,'unauthenticated_api_denied':True,
                'scope':'real Grafana/private HTTP exporter with synthetic traffic; not production monitoring rollout'}
            (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result),flush=True)
            success=True
    finally:
        if not (keep and success):
            for name in reversed(names): docker('rm','-f',name)
            if network: docker('network','rm',prefix)
        # Preserve only this owned evidence directory for inspection; no broad cleanup.


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-image',required=True,help='Explicit locally built application image; no historical image fallback')
    parser.add_argument('--grafana-image',required=True,help='Explicit scanned image built from ops/grafana/Dockerfile')
    parser.add_argument('--keep',action='store_true',help='Keep successful isolated stack for browser acceptance')
    args=parser.parse_args()
    main(args.app_image,args.grafana_image,args.keep)
