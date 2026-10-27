import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest


def fixture_module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parent/'fixtures'/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name',('linux_lab_setup','linux_lab_start_api','linux_lab_install_units','linux_lab_fault_control'))
def test_lab_setup_cannot_run_on_unrelated_machine(name,monkeypatch):
    module=fixture_module(name)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'not-an-owned-guest')
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:pytest.fail('unexpected system operation'))
    with pytest.raises(RuntimeError,match='guest|node1'):
        module.main()


def test_lab_runtime_files_are_create_only(tmp_path,monkeypatch):
    module=fixture_module('linux_lab_setup')
    monkeypatch.setattr(module,'STATE',tmp_path)
    file=module.private('synthetic',b'non-secret fixture')
    assert file.read_bytes()==b'non-secret fixture'
    with pytest.raises(FileExistsError):
        module.private('synthetic',b'replacement')


def test_lab_command_failure_does_not_echo_sensitive_output(monkeypatch):
    module=fixture_module('linux_lab_setup')
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(
        returncode=1,stdout='synthetic-sensitive',stderr='synthetic-sensitive'))
    with pytest.raises(RuntimeError) as caught:
        module.command(['synthetic-tool'],text='synthetic-sensitive')
    assert 'sensitive' not in str(caught.value)


def test_fault_oracle_rejects_untrusted_sql_identifier(monkeypatch):
    module=fixture_module('linux_lab_fault_control')
    monkeypatch.setattr(module.socket,'gethostname',lambda:'cs-s2s6-node1')
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:pytest.fail('must reject before SQL'))
    with pytest.raises(ValueError):
        module.audit("not-a-uuid'; SELECT 1")


@pytest.mark.parametrize('case,changes',[
    ('pre',{}),('pre',{'node2_answer':False}),('pre',{'assistants':2}),
    ('model',{}),('model',{'attempts':2}),('model',{'started_attempts':0}),
])
def test_fault_assertions_distinguish_recovery_from_blind_replay(case,changes,monkeypatch):
    module=fixture_module('linux_lab_fault_control')
    row=({'status':'complete','fence':3,'attempts':1,'assistants':1,'done':1,
          'requeues':1,'snapshots':1,'node2_answer':True} if case=='pre' else
         {'status':'interrupted','fence':2,'attempts':1,'started_attempts':1,'assistants':0,
          'done':0,'interrupted':1,'requeues':0,'snapshots':1,'error_code':'lease_expired'})
    row.update(changes)
    monkeypatch.setattr(module,'audit',lambda rid:row)
    if changes:
        with pytest.raises(AssertionError): module.wait('synthetic',case)
    else:
        assert module.wait('synthetic',case)==row


def test_sse_probe_refuses_unrelated_host_before_reading_runtime(monkeypatch):
    module=fixture_module('linux_sse_fault_probe')
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable'):
        module.Client()


def test_sse_probe_parses_actual_frames_not_payload_substrings():
    module=fixture_module('linux_sse_fault_probe')
    parsed=module.frames('id: 4\nevent: token\ndata: {"text":"event: done"}\n\nid: 5\nevent: done\ndata: {}\n\n')
    assert parsed==[{'id':4,'event':'token'},{'id':5,'event':'done'}]
    with pytest.raises(ValueError):
        module.frames('id: not-a-cursor\nevent: token\n\n')


def test_load_balancer_guard_precedes_runtime_reads(monkeypatch):
    module=fixture_module('linux_lab_load_balancer')
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node1'):
        module.main()


def test_ambiguous_post_probe_rejects_unowned_guest(monkeypatch):
    module=fixture_module('linux_lb_post_fault')
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node1'):
        module.main()


def test_network_dropin_refuses_unowned_guest_before_writes(monkeypatch):
    module=fixture_module('linux_lab_persist_network')
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main()


def test_network_dropin_only_adds_test_address_and_pg_source_route():
    module=fixture_module('linux_lab_persist_network')
    assert 'Address=172.24.168.125/20' in module.CONFIG
    assert 'Destination=172.24.168.13/32' in module.CONFIG
    assert 'PreferredSource=172.24.168.125' in module.CONFIG
    assert 'DHCP=' not in module.CONFIG and 'Gateway=' not in module.CONFIG


def test_load_balancer_requires_verified_tls_and_no_nonidempotent_replay():
    from pathlib import Path
    template=(Path(__file__).resolve().parents[1]/'ops/nginx/customer-service-lb.conf.template').read_text()
    directives='\n'.join(line for line in template.splitlines() if not line.lstrip().startswith('#'))
    assert 'proxy_ssl_verify on;' in directives
    assert 'proxy_ssl_name @CANONICAL_HOST@;' in directives
    assert 'non_idempotent' not in directives
    assert 'proxy_next_upstream_tries 2;' in directives
    assert 'proxy_buffering off;' in directives
