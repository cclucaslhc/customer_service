"""Non-mutating capability evidence; never bypass parser sandbox policy."""
import ctypes
import json
import platform
import sys


def main():
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise RuntimeError('Linux x86_64 required')
    libc=ctypes.CDLL(None,use_errno=True)
    abi=libc.syscall(444,0,0,1)
    print(json.dumps({'kernel':platform.release(),'landlock_abi':abi,
        'errno':ctypes.get_errno() if abi<0 else 0,'required_abi':5,
        'parser_kernel_supported':abi>=5}))


if __name__=='__main__': main()
