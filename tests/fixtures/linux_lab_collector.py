"""Create-only real Collector + API OTLP wiring on the authorized node2."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/home/labops/cs-lab/state')
BINARY=Path('/home/labops/cs-lab/otelcol-contrib')
EXPECTED='3530d0bd121cc54b2390e00a1b11e2fdfd56c528584d8b4cbd2bfb7b082bcf5f'


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if result.returncode: raise RuntimeError('Lab Collector operation failed: '+args[0])
    return result.stdout.strip()


def create(path,text):
    if path.parent.is_symlink(): raise RuntimeError('Refusing symlink deployment directory')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as output: output.write(text)
    path.chmod(0o644)


def main():
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node2 required')
    import pwd
    if os.geteuid()!=0: raise RuntimeError('Explicit guest root installation required')
    if json.loads((STATE/'public.json').read_text())['database']!='cs_test_two_nodes_20261026':
        raise RuntimeError('Exact disposable database required')
    if BINARY.is_symlink() or hashlib.sha256(BINARY.read_bytes()).hexdigest()!=EXPECTED:
        raise RuntimeError('Official pinned image binary hash mismatch')
    if Path('/opt/cs-s2-otel').exists() or Path('/etc/systemd/system/cs-lab-otel.service').exists():
        raise RuntimeError('Refusing existing Collector deployment')
    try: pwd.getpwnam('cs-s2-otel')
    except KeyError: pass
    else: raise RuntimeError('Refusing existing service identity')
    for port in (4318,13133):
        with socket.socket() as check: check.bind(('127.0.0.1',port))
    api=Path('/etc/systemd/system/cs-lab-api.service').read_text()
    starts=[line for line in api.splitlines() if line.startswith('ExecStart=')]
    if (len(starts)!=1 or 'scripts/serve_api.py' not in starts[0] or '--json-http-logs' not in starts[0]
            or '--otlp-traces-endpoint' in starts[0]):
        raise RuntimeError('Expected exact prior lab API entrypoint required')
    run(['useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent',
         '--shell','/usr/sbin/nologin','cs-s2-otel'])
    Path('/opt/cs-s2-otel').mkdir(mode=0o755)
    run(['install','-o','root','-g','root','-m','755',str(BINARY),'/opt/cs-s2-otel/otelcol-contrib'])
    config=(ROOT/'ops/otel/collector.yaml').read_text().replace(
        '/var/lib/customer-service-otel/','/var/lib/cs-s2-otel/')
    create(Path('/etc/cs-s2-otel/collector.yaml'),config)
    unit=(ROOT/'ops/systemd/customer-service-otel.service.template').read_text()
    unit=unit.replace('customer-service-otel','cs-s2-otel').replace(
        '/etc/customer-service/otel-collector.yaml','/etc/cs-s2-otel/collector.yaml').replace(
        'LogNamespace=customer-service','LogNamespace=cs-s2-lab')
    create(Path('/etc/systemd/system/cs-lab-otel.service'),unit)
    run(['/opt/cs-s2-otel/otelcol-contrib','validate','--config=/etc/cs-s2-otel/collector.yaml'])
    run(['systemd-analyze','verify','/etc/systemd/system/cs-lab-otel.service'])
    run(['systemctl','daemon-reload'])
    run(['systemctl','start','cs-lab-otel'])
    deadline=time.monotonic()+20
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            with opener.open('http://127.0.0.1:13133',timeout=1) as response:
                if response.status==200: break
        except OSError: pass
        if time.monotonic()>deadline: raise RuntimeError('Collector startup health failed; API not modified')
        time.sleep(.2)
    create(Path('/etc/systemd/system/cs-lab-api.service.d/40-s2-traces.conf'),
        '[Service]\nExecStart=\n'+starts[0]+' --otlp-traces-endpoint http://127.0.0.1:4318/v1/traces\n')
    run(['systemctl','daemon-reload'])
    run(['systemctl','restart','cs-lab-api'])
    print(json.dumps({'collector_active':run(['systemctl','is-active','cs-lab-otel'])=='active',
        'api_active':run(['systemctl','is-active','cs-lab-api'])=='active',
        'binary_sha256':EXPECTED,'identity':'cs-s2-otel','not_enabled_at_boot':True}))


def wire_worker():
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node2 required')
    if os.geteuid()!=0: raise RuntimeError('Explicit guest root installation required')
    assert run(['systemctl','is-active','cs-lab-otel'])=='active'
    source=Path('/etc/systemd/system/cs-lab-worker.service').read_text()
    starts=[line for line in source.splitlines() if line.startswith('ExecStart=')]
    if len(starts)!=1 or 'tests/fixtures/linux_lab_worker.py' not in starts[0] or '--otlp-traces-endpoint' in starts[0]:
        raise RuntimeError('Expected exact prior synthetic worker required')
    create(Path('/etc/systemd/system/cs-lab-worker.service.d/40-s2-traces.conf'),
        '[Service]\nExecStart=\n'+starts[0]+' --otlp-traces-endpoint http://127.0.0.1:4318/v1/traces\n')
    run(['systemctl','daemon-reload'])
    run(['systemctl','restart','cs-lab-worker'])
    print('Existing synthetic worker wired to private Collector')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wire-worker',action='store_true')
    if parser.parse_args().wire_worker: wire_worker()
    else: main()
