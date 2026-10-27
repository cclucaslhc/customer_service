"""Run in an owned Linux process/container, never sandbox the pytest parent."""
import os
import ctypes
import errno
from pathlib import Path
import socket
import tempfile

from app.parser_sandbox import restrict_parser


with tempfile.TemporaryDirectory(prefix='cs-sandbox-synthetic-') as outer:
    outer = Path(outer)
    secret = outer/'synthetic-private.txt'
    secret.write_text('Synthetic unrelated service file, not a real credential.')
    workspace = outer/'cs-parser-owned'
    workspace.mkdir()
    (workspace/'input').write_bytes(b'Synthetic allowed input.')
    sandbox_child = os.fork()
    if sandbox_child == 0:
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        restrict_parser(workspace)
        libc = ctypes.CDLL(None, use_errno=True)
        # Reject x32 socket numbers and SysV IPC rather than allowing alternate
        # communication paths around the native socket syscall deny list.
        for number in (41 | 0x40000000, 68):
            assert libc.syscall(number, 0, 0, 0) == -1
            assert ctypes.get_errno() == errno.EPERM
        assert (workspace/'input').read_bytes() == b'Synthetic allowed input.'
        (workspace/'output').write_bytes(b'Allowed output')
        for operation in (lambda: secret.read_bytes(), lambda: secret.write_bytes(b'not allowed'),
                          lambda: (outer/'outside').write_bytes(b'not allowed'),
                          lambda: socket.socket(), lambda: listener.accept(), os.setsid,
                          lambda: os.setpgid(0, 0), lambda: os.kill(os.getppid(), 0)):
            try:
                operation()
            except PermissionError:
                pass
            else:
                raise AssertionError('Sandbox allowed a forbidden operation')
        listener.close()
        # A descendant cannot remove the inherited restrictions.
        child = os.fork()
        if child == 0:
            try:
                secret.read_bytes()
            except PermissionError:
                os._exit(0)
            os._exit(1)
        assert os.waitpid(child, 0)[1] == 0
        os._exit(0)
    assert os.waitpid(sandbox_child, 0)[1] == 0
    print('filesystem/network/group and inherited sandbox checks passed', flush=True)
    # Only the unrestricted test parent cleans its owned synthetic directory.
