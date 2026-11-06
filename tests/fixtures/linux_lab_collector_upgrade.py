"""One-time rollback-safe upgrade of the explicitly owned node2 Collector."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

OLD='3530d0bd121cc54b2390e00a1b11e2fdfd56c528584d8b4cbd2bfb7b082bcf5f'
NEW='57911a9d902cfe2c287dac09fcee4b054630b4ec248fa190a3115d860a32f81f'
ACTIVE=Path('/opt/cs-s2-otel/otelcol-contrib')
BACKUP=Path('/opt/cs-s2-otel/otelcol-contrib.before-0.164.0')
PENDING=Path('/opt/cs-s2-otel/otelcol-contrib.pending-0.164.0')
SOURCE=Path('/home/labops/cs-lab/otelcol-contrib-0.164.0')
CONFIG='/etc/cs-s2-otel/collector.yaml'
READY=Path('/var/lib/cs-lab-ready')


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if result.returncode: raise RuntimeError('Isolated Collector upgrade operation failed')
    return result.stdout.strip()


def check_file(path,expected):
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('Exact pinned Collector binary required')


def main():
    if socket.gethostname()!='cs-s2s6-node2' or not READY.is_file():
        raise RuntimeError('Owned disposable node2 required')
    if os.geteuid()!=0: raise RuntimeError('Guest root required')
    check_file(ACTIVE,OLD)
    check_file(SOURCE,NEW)
    if ACTIVE.parent.is_symlink() or BACKUP.exists() or BACKUP.is_symlink() or PENDING.exists() or PENDING.is_symlink():
        raise RuntimeError('Refusing existing backup or pending upgrade')
    assert run(['systemctl','is-active','cs-lab-otel'])=='active'
    run(['install','-o','root','-g','root','-m','755',str(SOURCE),str(PENDING)])
    check_file(PENDING,NEW)
    run([str(PENDING),'validate','--config='+CONFIG])
    run(['systemctl','stop','cs-lab-otel'])
    swapped=False
    try:
        os.replace(ACTIVE,BACKUP)
        swapped=True
        os.replace(PENDING,ACTIVE)
        run(['systemctl','start','cs-lab-otel'])
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+20
        while True:
            try:
                with opener.open('http://127.0.0.1:13133',timeout=1) as response:
                    if response.status==200: break
            except OSError: pass
            if time.monotonic()>deadline: raise RuntimeError('Upgraded Collector health failed')
            time.sleep(.2)
        check_file(ACTIVE,NEW)
        check_file(BACKUP,OLD)
    except Exception:
        if swapped:
            run(['systemctl','stop','cs-lab-otel'])
            os.replace(BACKUP,ACTIVE)
        run(['systemctl','start','cs-lab-otel'])
        raise
    print(json.dumps({'collector_version':'0.164.0','binary_sha256':NEW,
        'health_verified':True,'old_binary_preserved':True,'config_and_state_preserved':True}))


if __name__=='__main__': main()
