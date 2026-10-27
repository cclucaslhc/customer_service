"""Start explicit production API entrypoint on either owned disposable guest."""
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/home/labops/cs-lab/state')


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=45)
    if result.returncode:
        raise RuntimeError('Lab start operation failed: '+args[0])


def main():
    if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable guest required')
    if json.loads((STATE/'public.json').read_text())['database']!='cs_test_two_nodes_20261026':
        raise RuntimeError('Exact disposable database required')
    data=Path('/home/labops/cs-lab/data')
    for name in ('docs','assets','legacy'):
        (data/name).mkdir(parents=True,exist_ok=True)
    template=(ROOT/'ops/nginx/customer-service.conf.template').read_text()
    for name,value in {'CANONICAL_HOST':'cs-lab.internal','TLS_CERTIFICATE_PATH':str(STATE/'https.crt'),
        'TLS_PRIVATE_KEY_PATH':str(STATE/'https.key'),'APPROVED_UPLOAD_LIMIT':'1m'}.items():
        template=template.replace('@'+name+'@',value)
    config=STATE/'nginx.conf'
    if config.exists():
        raise RuntimeError('Refusing to replace existing API deployment')
    config.write_text('pid '+str(STATE/'nginx.pid')+';\nerror_log stderr warn;\n'
        'events { worker_connections 128; }\nhttp { access_log off;\n'+template+'\n}\n')
    config.chmod(0o600)
    run(['sudo','/usr/sbin/nginx','-t','-c',str(config)])
    run(['sudo','systemctl','stop','nginx'])
    run(['sudo','systemd-run','--unit=cs-lab-api','--property=User=labops',
        '--property=WorkingDirectory='+str(ROOT),'--property=Restart=no',
        str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/serve_api.py'),
        '--database-url-file',str(STATE/'api-url'),'--master-key-path',str(STATE/'master.key'),
        '--origin','https://cs-lab.internal','--knowledge-root',str(data/'docs'),
        '--asset-root',str(data/'assets'),'--legacy-root',str(data/'legacy'),
        '--storage-policy',str(STATE/'storage-policy.json'),'--json-http-logs'])
    run(['sudo','systemd-run','--unit=cs-lab-edge','--property=Restart=no',
        '/usr/sbin/nginx','-c',str(config),'-g','daemon off;'])
    print(json.dumps({'node':socket.gethostname(),'api':'127.0.0.1:8000',
        'edge':'private-guest:443','origin':'https://cs-lab.internal','models':'not configured'}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Lab API start failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
