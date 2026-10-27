import json
from types import SimpleNamespace
import pytest
from scripts import rag_rollout_watch as watch


def control():
    return dict(tenant_id='tenant',rollout_id='rollout',expected_rollout_version=1,
        expected_plan_sha256='a'*64,expected_release_version=2,expected_body_sha256='b'*64,
        current_binding={'prompt_sha256':'c'*64})


def test_default_once_and_explicit_repeat_stop_on_pause(monkeypatch):
    reads=[]
    def load(path):
        reads.append(path)
        return control()
    monkeypatch.setattr(watch,'load_control',load)
    decisions=iter(('hold','healthy','pause'))
    calls=[]
    def tick(**args):
        calls.append(args)
        return dict(state='running',version=1,health=dict(decision=next(decisions),reason_codes=[]))
    sleeps,output=[],[]
    assert watch.run_checks(SimpleNamespace(check_health_and_pause=tick),'explicit',checks=5,
        interval_seconds=2,sleep=sleeps.append,emit=output.append)==2
    assert len(calls)==len(reads)==3 and sleeps==[2,2]
    assert all('current_binding' not in item and 'tenant' not in item for item in output)


def test_failure_is_not_retried_or_reported_as_pause(monkeypatch):
    monkeypatch.setattr(watch,'load_control',lambda path:control())
    def fail(**args):
        raise ValueError('stale receipt')
    with pytest.raises(ValueError,match='stale'):
        watch.run_checks(SimpleNamespace(check_health_and_pause=fail),'explicit',checks=2,
            interval_seconds=1,sleep=lambda _:pytest.fail('must not retry'),emit=lambda _:pytest.fail('no success'))


@pytest.mark.parametrize('checks,interval',[(0,1),(True,1),(1,0),(1,61),(1000001,1)])
def test_invalid_schedule_never_checks(checks,interval):
    with pytest.raises(ValueError):
        watch.run_checks(None,None,checks=checks,interval_seconds=interval)


def test_control_strict_shape_duplicates_and_private_reader(monkeypatch):
    monkeypatch.setattr(watch,'read_private_line',lambda path,maximum:json.dumps(control()).encode())
    assert watch.load_control('explicit')==control()
    monkeypatch.setattr(watch,'read_private_line',lambda path,maximum:b'{"tenant_id":1,"tenant_id":2}')
    with pytest.raises(ValueError,match='Duplicate'):
        watch.load_control('explicit')
    monkeypatch.setattr(watch,'read_private_line',lambda path,maximum:b'{}')
    with pytest.raises(ValueError):
        watch.load_control('explicit')
