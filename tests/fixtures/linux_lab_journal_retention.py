"""Accelerated real journald retention, in a new synthetic-only namespace."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if result.returncode: raise RuntimeError('Synthetic journal retention operation failed')
    return result.stdout


def main():
    if socket.gethostname()!='cs-s2s6-node2' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node2 required')
    if os.geteuid()!=0: raise RuntimeError('Explicit guest root required')
    suffix=uuid.uuid4().hex[:12]
    namespace='cs-s2-retention-'+suffix
    directory=Path('/etc/systemd/journald@'+namespace+'.conf.d')
    directory.mkdir(mode=0o755,exist_ok=False)
    config=directory/'10-synthetic-retention.conf'
    with config.open('x') as target:
        target.write('[Journal]\nStorage=persistent\nSplitMode=none\n'
            'MaxRetentionSec=2s\nMaxFileSec=1s\nSystemMaxUse=32M\nSystemKeepFree=1G\n'
            'ForwardToSyslog=no\nForwardToKMsg=no\nForwardToConsole=no\n')
    config.chmod(0o644)
    def emit(marker):
        run(['systemd-run','--quiet','--wait','--collect',
            '--unit=cs-s2-retention-write-'+uuid.uuid4().hex[:12],
            '--property=LogNamespace='+namespace,'/usr/bin/printf','%s\n',marker])
        run(['journalctl','--namespace='+namespace,'--sync'])
    def present(marker):
        # Read only the namespace created above, never the host's global journal.
        output=run(['journalctl','--namespace='+namespace,'--no-pager','-o','cat'])
        return marker in output
    first='synthetic-expired-'+suffix
    second='synthetic-current-'+suffix
    holder='cs-s2-retention-holder-'+suffix
    try:
        # Keep the namespace's journal service required while short-lived writers
        # exit; otherwise systemd may stop it before its private sync endpoint.
        run(['systemd-run','--quiet','--collect','--unit='+holder,
            '--property=LogNamespace='+namespace,'/usr/bin/sleep','60'])
        emit(first)
        assert present(first),'First synthetic record missing before expiry'
        run(['journalctl','--namespace='+namespace,'--rotate'])
        time.sleep(4)
        # Retention is archive/file based, not a per-message TTL. Rotate the
        # now-old active file before producing the fresh comparison record.
        run(['journalctl','--namespace='+namespace,'--rotate'])
        emit(second)
        assert present(second),'Recent synthetic record lost'
        assert not present(first),'Configured retention did not remove expired archive'
        print(json.dumps({'namespace':namespace,'retention_seconds':2,
            'expired_archive_removed':True,'recent_record_preserved':True,
            'manual_vacuum_used':False,'global_or_existing_namespace_modified':False,
            'scope':'accelerated isolated retention mechanism, not 14 elapsed production days'}))
    finally:
        # Keep synthetic records/config for evidence; only stop the owned namespace.
        run(['systemctl','stop',holder])
        run(['systemctl','stop','systemd-journald@'+namespace+'.socket',
            'systemd-journald@'+namespace+'.service'])


if __name__=='__main__': main()
