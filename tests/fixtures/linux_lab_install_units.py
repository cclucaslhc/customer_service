"""Install restartable, manual-start-only units on the two owned lab guests."""
from pathlib import Path
import shlex
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/home/labops/cs-lab/state')
DATA=Path('/home/labops/cs-lab/data')


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=45)
    if result.returncode:
        raise RuntimeError('Lab unit operation failed: '+args[0])


def main():
    if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable guest required')
    api=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/serve_api.py'),
        '--database-url-file',str(STATE/'api-url'),'--master-key-path',str(STATE/'master.key'),
        '--origin','https://cs-lab.internal','--knowledge-root',str(DATA/'docs'),
        '--asset-root',str(DATA/'assets'),'--legacy-root',str(DATA/'legacy'),
        '--storage-policy',str(STATE/'storage-policy.json'),'--json-http-logs']
    commands={'cs-lab-api':('labops',api),
        'cs-lab-worker':('labops',[str(ROOT/'.venv/bin/python'),str(ROOT/'tests/fixtures/linux_lab_worker.py'),'--delay','1']),
        'cs-lab-edge':('root',['/usr/sbin/nginx','-c',str(STATE/'nginx.conf'),'-g','daemon off;'])}
    staged=[]
    for name,(user,args) in commands.items():
        destination=Path('/etc/systemd/system')/(name+'.service')
        if destination.exists():
            raise RuntimeError('Refusing to replace an existing installed unit')
        source=STATE/(name+'.service')
        text=('[Unit]\nDescription=Disposable two-guest acceptance only\nAfter=network-online.target\n'
            '[Service]\nType=simple\nUser='+user+'\nWorkingDirectory='+str(ROOT)+'\n'
            'ExecStart='+shlex.join(args)+'\nRestart=no\nTimeoutStopSec=15\nUMask=0077\n')
        if user=='labops':
            text+='NoNewPrivileges=yes\nProtectSystem=strict\nReadWritePaths='+str(DATA)+' '+str(STATE)+'\nCapabilityBoundingSet=\n'
        with source.open('x',encoding='utf-8') as output:
            output.write(text)
        source.chmod(0o600)
        staged.append((source,destination))
    run(['sudo','systemd-analyze','verify',*[str(source) for source,_ in staged]])
    for source,destination in staged:
        run(['sudo','install','-o','root','-g','root','-m','644',str(source),str(destination)])
    # Only these explicitly owned lab units are stopped; no nginx/PG/Redis or
    # other host service is restarted here. Old transient units can be GC'd.
    run(['sudo','systemctl','stop',*commands])
    run(['sudo','systemctl','daemon-reload'])
    run(['sudo','systemctl','start',*commands])
    print('Lab API/edge/synthetic-worker units installed and started; not enabled at boot')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Lab unit install failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
