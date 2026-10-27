import json
import subprocess
from types import SimpleNamespace
import pytest
from scripts import verify_container_limits as module


@pytest.mark.parametrize('user', ['', '0', 'root', '0:123', 'root:123'])
def test_resource_probe_refuses_root_before_creating_any_container(monkeypatch,user):
    calls=[]
    def fake(*args,**kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps([{'Os':'linux','Config':{'User':user}}]))
    monkeypatch.setattr(module,'command',fake)
    with pytest.raises(ValueError,match='non-root'):
        module.verify('synthetic-image')
    assert calls == [('image','inspect','synthetic-image')]


def test_resource_probe_cleans_only_its_own_container_on_timeout(monkeypatch):
    calls=[]
    def fake(*args,**kwargs):
        calls.append(args)
        if args[0] == 'image':
            return SimpleNamespace(stdout=json.dumps([{'Id':'sha256:synthetic',
                'Os':'linux','Config':{'User':'65532:65532'}}]))
        if args[0] == 'start':
            raise subprocess.TimeoutExpired('synthetic docker peer',30)
        return SimpleNamespace(stdout='synthetic',returncode=0)
    monkeypatch.setattr(module,'command',fake)
    with pytest.raises(subprocess.TimeoutExpired):
        module.verify('synthetic-image')
    created=calls[1]
    name=created[created.index('--name')+1]
    assert name.startswith('cs-runtime-limits-')
    assert '--pull=never' in created and '--memory=128m' in created
    assert '--memory-swap=128m' in created and '--pids-limit=16' in created
    assert '--network=none' in created and '--read-only' in created
    assert 'sha256:synthetic' in created
    assert not any('mount' in item or item == '--privileged' for item in created)
    assert calls[-1] == ('rm','-f',name)
