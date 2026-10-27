"""Create-only, private TLS LB for the two explicitly authorized lab guests."""
import json
from pathlib import Path
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STATE = Path('/home/labops/cs-lab/state')


def main():
    if socket.gethostname() != 'cs-s2s6-node1' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node1 required')
    if json.loads((STATE/'public.json').read_text())['database'] != 'cs_test_two_nodes_20261026':
        raise RuntimeError('Exact disposable database required')
    template = (ROOT/'ops/nginx/customer-service-lb.conf.template').read_text()
    for name,value in {
        'PRIMARY_EDGE':'172.24.168.13:443','BACKUP_EDGE':'172.24.168.125:443',
        'PRIVATE_LISTEN':'172.24.168.13:8443','CANONICAL_HOST':'cs-lab.internal',
        'TLS_CERTIFICATE_PATH':str(STATE/'https.crt'),
        'TLS_PRIVATE_KEY_PATH':str(STATE/'https.key'),
        'UPSTREAM_CA_PATH':str(STATE/'pg-ca.crt'),'APPROVED_UPLOAD_LIMIT':'1m',
    }.items():
        template = template.replace('@'+name+'@',value)
    config = STATE/'nginx-lb.conf'
    with config.open('x') as target:
        target.write('pid '+str(STATE/'nginx-lb.pid')+';\nerror_log stderr warn;\n'
                     'events { worker_connections 128; }\nhttp { access_log off;\n'+template+'\n}\n')
    config.chmod(0o600)
    commands = [
        ['sudo','nginx','-t','-c',str(config)],
        ['sudo','systemd-run','--unit=cs-lab-lb','--property=Restart=no',
         '/usr/sbin/nginx','-c',str(config),'-g','daemon off;'],
    ]
    for command in commands:
        result = subprocess.run(command,capture_output=True,text=True,timeout=30)
        if result.returncode:
            raise RuntimeError('Lab LB operation failed: '+command[1])
    print('Private TLS lab LB started on node1:8443; no VIP or LB HA')


if __name__ == '__main__':
    main()
