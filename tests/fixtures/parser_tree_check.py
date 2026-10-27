"""Linux process-group termination proof using only synthetic sleeping children."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app.isolated_parser as module
from app.knowledge_models import DocumentParseError


def verify(mode):
    original = subprocess.Popen
    processes = []
    with tempfile.TemporaryDirectory(prefix='cs-parser-tree-proof-') as directory:
        pidfile = Path(directory)/'child.pid'
        code = ('import os,time; child=os.fork(); '
                'time.sleep(30) if child == 0 else None; '
                'os._exit(0) if child == 0 else None; '
                f'open({str(pidfile)!r},"w").write(str(child)); '
                + ('os._exit(2)' if mode == 'exit' else 'time.sleep(30)'))
        def launch(command, **kwargs):
            process = original([sys.executable, '-c', code], **kwargs)
            processes.append(process)
            return process
        def check():
            if pidfile.exists() and pidfile.stat().st_size:
                raise PermissionError('Synthetic authority lost')
        try:
            with patch.object(module.subprocess, 'Popen', launch):
                try:
                    module.IsolatedDocumentParser(timeout_seconds=1 if mode == 'timeout' else 10)(
                        'synthetic.txt', b'synthetic',
                        execution_check=check if mode == 'authority' else None)
                except (DocumentParseError, PermissionError):
                    pass
                else:
                    raise AssertionError('Parser did not stop')
            assert len(processes) == 1 and processes[0].poll() is not None
            assert pidfile.exists(), 'Synthetic grandchild was not started'
            pid = int(pidfile.read_text())
            for attempt in range(50):
                stat = Path(f'/proc/{pid}/stat')
                if not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    break
                time.sleep(.01)
            else:
                raise AssertionError('Parser grandchild survived termination')
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if pidfile.exists():
                try:
                    pid = int(pidfile.read_text())
                    if processes and os.getpgid(pid) == processes[0].pid:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    assert sys.platform == 'linux'
    for mode in ('timeout', 'authority', 'exit'):
        verify(mode)
    print('Parser process-group termination OK')
