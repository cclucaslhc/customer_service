"""Bounded fault injection into owned local containers; never a production target.

No host mounts, credentials, network, privileged mode or Docker socket in peers.
The image must already exist locally. This does not certify host/cluster HA.
"""
import argparse
import json
import subprocess
import uuid


def command(*args, check=True, timeout=30):
    result = subprocess.run(['docker', *map(str,args)],capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Docker command failed')
    return result


PIDS = '''import errno,json,os,signal,time
children=[]
blocked=False
try:
    for attempt in range(64):
        try:
            pid=os.fork()
        except OSError as exc:
            assert exc.errno == errno.EAGAIN
            blocked=True
            break
        if pid == 0:
            time.sleep(20)
            os._exit(0)
        children.append(pid)
    assert blocked and 0 < len(children) < 16
    print(json.dumps({'pid_limit_enforced':True,'children':len(children)}),flush=True)
finally:
    for pid in children:
        os.kill(pid,signal.SIGKILL)
    for pid in children:
        os.waitpid(pid,0)
'''

DISK_CPU = '''import errno,json,time
from pathlib import Path
full=False
try:
    with open('/tmp/synthetic-fill','wb',buffering=0) as output:
        for attempt in range(8):
            output.write(b'x'*(1024*1024))
except OSError as exc:
    assert exc.errno == errno.ENOSPC
    full=True
assert full
quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert 0 < quota/period <= .25
def throttled():
    return dict(line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())
before=int(throttled()['nr_throttled'])
deadline=time.monotonic()+1.5
value=0
while time.monotonic()<deadline:
    value=(value+1)%1000000
assert int(throttled()['nr_throttled']) > before
print(json.dumps({'tmpfs_limit_enforced':True,'cpu_throttling_observed':True}),flush=True)
'''


def verify(image, python_binary='/opt/customer-service/.venv/bin/python'):
    identity = json.loads(command('image','inspect',image).stdout)[0]
    if identity['Os'] != 'linux' or identity['Config']['User'].split(':',1)[0] in ('', '0', 'root'):
        raise ValueError('Expected an existing non-root Linux application image')
    evidence = {'image_id':identity['Id']}
    cases = [('memory', "a=bytearray(256*1024*1024); print('unexpected allocation')"),
             ('pids', PIDS), ('disk_cpu', DISK_CPU)]
    for label,script in cases:
        name = 'cs-runtime-limits-'+uuid.uuid4().hex
        created = False
        try:
            command('create','--pull=never','--name',name,'--network=none','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=128m',
                '--memory-swap=128m','--pids-limit=16','--cpus=.25','--tmpfs=/tmp:rw,size=4m',
                '--entrypoint',python_binary,identity['Id'],'-c','import os; assert os.geteuid() != 0\n'+script)
            created = True
            outcome = command('start','--attach',name,check=False)
            state = json.loads(command('inspect',name).stdout)[0]
            config = state['HostConfig']
            assert config['Memory'] == config['MemorySwap'] == 128*1024*1024
            assert config['PidsLimit'] == 16 and config['ReadonlyRootfs'] is True
            assert config['NetworkMode'] == 'none'
            if label == 'memory':
                assert outcome.returncode != 0 and state['State']['OOMKilled'] is True
                assert state['State']['ExitCode'] == 137
                evidence[label] = {'oom_killed':True,'exit_code':137}
            else:
                assert outcome.returncode == 0, outcome.stderr
                evidence[label] = json.loads(outcome.stdout)
        finally:
            if created:
                command('rm','-f',name)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True,help='Existing local non-root application image; never pulls')
    args = parser.parse_args()
    print(json.dumps(verify(args.image),sort_keys=True))
