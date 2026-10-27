"""Linux parser-only Landlock and seccomp; never weaken the service parent.

Requires x86_64 and Landlock ABI >= 5. Unsupported kernels fail closed.
Not a VM boundary: keep runtime code immutable and enforce container limits.
"""
import ctypes
import errno
import os
from pathlib import Path
import platform
import stat
import sys


class SandboxUnavailable(RuntimeError):
    pass


def restrict_parser(workspace):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise SandboxUnavailable('Parser sandbox requires Linux x86_64')
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir() or not workspace.name.startswith('cs-parser-'):
        raise SandboxUnavailable('Expected a private parser workspace')
    libc = ctypes.CDLL(None, use_errno=True)

    def checked(value):
        if value < 0:
            raise SandboxUnavailable(f'Parser sandbox unavailable (errno {ctypes.get_errno()})')
        return value

    abi = checked(libc.syscall(444, 0, 0, 1))
    if abi < 5:
        raise SandboxUnavailable('Landlock ABI >= 5 required')

    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]

    # All filesystem rights through IOCTL_DEV (ABI 5); newer ABI rights are
    # intentionally not assumed. Rules apply to descendants and exec as well.
    all_rights = (1 << 16)-1
    read_rights = (1 << 0) | (1 << 2) | (1 << 3)
    ruleset = Ruleset(all_rights)
    rules_fd = checked(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0))
    try:
        readonly = [Path('/usr'), Path('/lib'), Path('/lib64'), Path(sys.prefix),
            Path(sys.base_prefix), Path(__file__).resolve().parent,
            Path('/etc/ld.so.cache'), Path('/etc/localtime'), Path('/etc/fonts'), Path('/etc/mime.types'),
            Path('/proc/cpuinfo'), Path('/proc/meminfo'), Path('/proc/self'),
            Path('/sys/devices/system/cpu'), Path('/dev/urandom'), Path('/dev/random')]
        # Only ordinary temporary files/directories: no executable files,
        # device nodes, sockets or FIFOs need to be created by a parser.
        workspace_rights = sum(1 << bit for bit in (1, 2, 3, 4, 5, 7, 8, 13, 14))
        grants = [(workspace, workspace_rights), (Path('/dev/null'), (1 << 1) | (1 << 2))]
        grants += [(path, read_rights) for path in readonly if path.exists()]
        seen = set()
        for path, rights in grants:
            path = path.resolve(strict=True)
            if path == Path('/'):
                raise SandboxUnavailable('Refusing broad parser filesystem grant')
            if (path, rights) in seen:
                continue
            seen.add((path, rights))
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    rights &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15)
                rule = PathRule(rights, fd)
                checked(libc.syscall(445, rules_fd, 1, ctypes.byref(rule), 0))
            finally:
                os.close(fd)
        checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(libc.syscall(446, rules_fd, 0))
    finally:
        os.close(rules_fd)

    class Filter(ctypes.Structure):
        _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                   ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(Filter))]

    kill, allow, deny = 0x80000000, 0x7fff0000, 0x00050000 | errno.EPERM
    # Verify native architecture and reject the x32 syscall-number namespace.
    instructions = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, 0xc000003e),
        Filter(0x06, 0, 0, kill), Filter(0x20, 0, 0, 0),
        Filter(0x35, 0, 1, 0x40000000), Filter(0x06, 0, 0, deny)]
    # Socket syscalls, io_uring network bypass, cross-process
    # FD/memory access, and escaping the parent's process-group cleanup.
    blocked = (set(range(29, 32)) | set(range(41, 56)) | set(range(64, 72))
        | set(range(240, 246)) | {62, 101, 109, 112, 129, 200, 220, 234, 272, 288, 297,
            298, 299, 302, 304, 307, 308, 310, 311, 321, 424, 425, 426, 427, 438, 440, 448})
    for number in sorted(blocked):
        instructions += [Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, deny)]
    instructions.append(Filter(0x06, 0, 0, allow))
    filters = (Filter * len(instructions))(*instructions)
    program = Program(len(instructions), filters)
    checked(libc.prctl(22, 2, ctypes.byref(program), 0, 0))  # SECCOMP_MODE_FILTER
