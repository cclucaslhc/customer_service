"""Create-only namespaced logging deployment on the authorized node2 API."""
import http.client
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/home/labops/cs-lab/state')
NAMESPACE='cs-s2-lab'
sys.path.insert(0,str(ROOT))


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError('Lab journal operation failed: '+args[0])
    return result.stdout


def install(path,text):
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError('Refusing symlink deployment target')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as output: output.write(text)
    path.chmod(0o644)


def main(verify_only=False,harden_existing=False):
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node2 required')
    if os.geteuid()!=0: raise RuntimeError('Explicit guest root installation required')
    if json.loads((STATE/'public.json').read_text())['database']!='cs_test_two_nodes_20261026':
        raise RuntimeError('Exact disposable database required')
    if harden_existing:
        desired=(ROOT/'ops/systemd/journald-customer-service.conf').read_text()
        target=Path('/etc/systemd/journald@cs-s2-lab.conf.d/10-retention.conf')
        if target.is_symlink() or target.read_text()!=desired.replace('SplitMode=none\n',''):
            raise RuntimeError('Expected exact previous lab retention config required')
        target.write_text(desired)
        command(['systemctl','stop','cs-lab-api'])
        command(['systemctl','stop','systemd-journald@'+NAMESPACE])
        machine=Path('/etc/machine-id').read_text().strip()
        import re
        if not re.fullmatch('[0-9a-f]{32}',machine): raise RuntimeError('Invalid machine ID')
        directory=Path('/var/log/journal')/(machine+'.'+NAMESPACE)
        if directory.is_symlink(): raise RuntimeError('Refusing symlink journal namespace')
        # Preserve records; revoke only this namespace's old per-user file access.
        for entry in directory.iterdir():
            if entry.is_symlink(): raise RuntimeError('Refusing symlink journal entry')
            if entry.is_file() and re.fullmatch(r'user-1000(?:@[0-9a-f-]+)?\.journal~?',entry.name):
                os.chown(entry,0,0)
                entry.chmod(0o600)
        command(['systemctl','start','systemd-journald@'+NAMESPACE])
        command(['systemctl','start','cs-lab-api'])
    if not verify_only:
        config=(ROOT/'ops/systemd/journald-customer-service.conf').read_text()
        install(Path('/etc/systemd/journald@cs-s2-lab.conf.d/10-retention.conf'),config)
        install(Path('/etc/systemd/system/cs-lab-api.service.d/30-s2-logging.conf'),
            '[Service]\nLogNamespace=cs-s2-lab\nStandardOutput=journal\nStandardError=journal\n')
        command(['systemctl','daemon-reload'])
        command(['systemctl','restart','cs-lab-api'])
    context=ssl.create_default_context(cafile=str(STATE/'pg-ca.crt'))
    sentinel='synthetic-sensitive-not-for-logs'
    def request(path):
        connection=http.client.HTTPSConnection('172.24.168.125',443,context=context,timeout=3)
        try:
            connection.request('GET',path,headers={'Host':'cs-lab.internal','X-Synthetic-Secret':sentinel})
            response=connection.getresponse()
            response.read(65536)
            return response.status
        finally: connection.close()
    deadline=time.monotonic()+25
    while True:
        try:
            if request('/api/auth/me?synthetic='+sentinel)==401: break
        except OSError: pass
        if time.monotonic()>deadline: raise RuntimeError('API failed readiness after namespace activation')
        time.sleep(.2)
    path_status=request('/api/'+sentinel)
    # Authentication may reject before routing; this probes redaction, not SPA
    # fallback semantics or whether unauthenticated clients can enumerate APIs.
    assert path_status in (401,404),path_status
    assert request('/api/auth/me?synthetic='+sentinel)==401
    from app.log_export import _safe_record
    while True:
        raw=command(['journalctl','--namespace='+NAMESPACE,'-u','cs-lab-api','--since','-2min','-o','json','--no-pager'])
        assert sentinel not in raw,'Synthetic query/header/path value leaked'
        records=[]
        for line in raw.splitlines():
            entry=json.loads(line)
            message=entry.get('MESSAGE','')
            try: record=json.loads(message)
            except (ValueError,TypeError): continue
            if isinstance(record,dict) and record.get('event')=='http.completed':
                assert _safe_record(record)
                records.append(record)
        if len(records)>=3: break
        if time.monotonic()>deadline: raise RuntimeError('No durable namespaced HTTP records')
        time.sleep(.2)
    denied=subprocess.run(['runuser','-u','labops','--','journalctl','--namespace='+NAMESPACE,
        '-u','cs-lab-api','--since','-2min','-o','json','--no-pager'],capture_output=True,text=True,timeout=10)
    assert not any(line.startswith('{') for line in denied.stdout.splitlines()),'API OS user can read journal'
    assert command(['systemctl','show','cs-lab-api','-p','LogNamespace','--value']).strip()==NAMESPACE
    assert command(['systemctl','is-active','cs-lab-api']).strip()=='active'
    print(json.dumps({'namespace':NAMESPACE,'valid_json_records':len(records),
        'synthetic_query_header_path_absent':True,'unprivileged_read_denied':True,
        'unmatched_api_status':path_status,
        'api_active':True,'retention_policy_configured':'14day/512M; not an elapsed 14-day expiry proof'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--harden-existing',action='store_true')
    args=parser.parse_args()
    if args.harden_existing and not args.verify_only:
        parser.error('Hardening must use --verify-only; never reinstall drop-ins')
    main(args.verify_only,args.harden_existing)
