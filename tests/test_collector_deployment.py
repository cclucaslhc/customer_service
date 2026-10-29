"""Static fail-closed Collector contracts; real binary validation is separate."""
from pathlib import Path
import yaml
import json
import re
from app.tracing import _ATTRS,_NAMES
import importlib.util
import pytest


def test_collector_private_output_uses_matching_non_root_host_identity(monkeypatch):
    spec=importlib.util.spec_from_file_location('collector_identity',ROOT/'scripts/verify_collector.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os,'getuid',lambda:1001,raising=False)
    monkeypatch.setattr(module.os,'getgid',lambda:1002,raising=False)
    assert module.runtime_user_args()==['--user','1001:1002']
    monkeypatch.setattr(module.os,'getuid',lambda:0)
    with pytest.raises(ValueError,match='non-root host user'):
        module.runtime_user_args()

ROOT=Path(__file__).resolve().parents[1]


def test_collector_is_loopback_bounded_and_not_a_debug_payload_exporter():
    config=yaml.safe_load((ROOT/'ops/otel/collector.yaml').read_text())
    http=config['receivers']['otlp']['protocols']['http']
    assert http['endpoint']=='127.0.0.1:4318'
    assert http['max_request_body_size']==1048576
    assert config['processors']['memory_limiter']['limit_mib']==192
    assert config['processors']['batch']['send_batch_max_size']==256
    assert list(config['exporters'])==['file/private']
    assert config['exporters']['file/private']['rotation']=={'max_megabytes':20,'max_days':7,'max_backups':3}
    assert config['processors']['filter/registered']['traces']['spanevent']==['true']
    assert config['processors']['transform/privacy']['error_mode']=='propagate'
    assert config['service']['pipelines']['traces']['processors'][-1]=='batch'
    pattern=config['processors']['filter/registered']['traces']['span'][0].split('"')[1]
    assert all(re.fullmatch(pattern,name) for name in _NAMES)
    assert not re.fullmatch(pattern,'httpXrequest')
    statements=config['processors']['transform/privacy']['trace_statements'][1]['statements']
    keys=json.loads(statements[0].split(', ',1)[1][:-1])
    assert set(keys)==_ATTRS


def test_collector_state_requires_separate_os_user_and_private_permissions():
    service=(ROOT/'ops/systemd/customer-service-otel.service.template').read_text()
    assert 'User=customer-service-otel' in service
    assert 'StateDirectoryMode=0700' in service and 'UMask=0077' in service
    assert 'CapabilityBoundingSet=\n' in service


def test_native_collector_install_rejects_foreign_guest_before_identity_changes(monkeypatch):
    spec=importlib.util.spec_from_file_location('linux_lab_collector',ROOT/'tests/fixtures/linux_lab_collector.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main()
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.wire_worker()


def test_trace_audit_refuses_foreign_guest_before_reading_trace_files(monkeypatch):
    spec=importlib.util.spec_from_file_location('linux_lab_trace_audit',ROOT/'tests/fixtures/linux_lab_trace_audit.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main('not-a-uuid')


def test_collector_acceptance_requires_explicit_application_image():
    import subprocess
    import sys
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_collector.py')],
        capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and '--app-image' in result.stderr
    source=(ROOT/'scripts/verify_collector.py').read_text(encoding='utf-8')
    assert "APP='sha256:" not in source
    assert '--collector-image' in result.stderr


def test_collector_security_rebuild_preserves_full_upstream_distribution():
    source=(ROOT/'ops/otel/Dockerfile').read_text(encoding='utf-8')
    assert 'bf626fce5c8053ac6ae9c6c5f4c408c8c182740e' in source
    assert 'go.opentelemetry.io/collector/cmd/builder@v0.164.0' in source
    assert '--config manifest.yaml --skip-compilation' in source
    assert 'go get github.com/go-jose/go-jose/v4@v4.1.6 && go mod verify' in source
    assert 'GOSUMDB="sum.golang.org https://sum.golang.org"' in source
    assert 'COPY --from=build /out/otelcol-contrib /otelcol-contrib' in source
    assert source.rstrip().endswith('USER 10001:10001')


def test_collector_inventory_rejects_removing_auth_or_changing_modules():
    from copy import deepcopy
    spec=importlib.util.spec_from_file_location('collector_components',ROOT/'scripts/verify_collector.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory={key:[{'name':'synthetic','module':'synthetic v1'}] for key in (
        'receivers','processors','exporters','extensions','connectors')}
    inventory['providers']=[{'scheme':'synthetic','module':'synthetic v1'}]
    inventory['extensions']=[{'name':'oidc','module':'synthetic-auth v1'}]
    candidate=deepcopy(inventory)
    candidate['buildinfo']={'version':'synthetic-security-rebuild'}
    assert module.compare_components(inventory,candidate)['extensions']==1
    candidate['extensions']=[]
    with pytest.raises(ValueError,match='differs from full upstream'):
        module.compare_components(inventory,candidate)
    candidate=deepcopy(inventory)
    candidate['receivers'][0]['module']='synthetic v2'
    with pytest.raises(ValueError,match='differs from full upstream'):
        module.compare_components(inventory,candidate)


def test_collector_upgrade_rejects_foreign_guest_before_reading_binary(monkeypatch):
    spec=importlib.util.spec_from_file_location('linux_lab_collector_upgrade',ROOT/'tests/fixtures/linux_lab_collector_upgrade.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main()


def test_collector_failed_start_restores_exact_old_binary(monkeypatch,tmp_path):
    import hashlib
    import shutil
    spec=importlib.util.spec_from_file_location('upgrade_rollback',ROOT/'tests/fixtures/linux_lab_collector_upgrade.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('ACTIVE','BACKUP','PENDING','SOURCE','READY'):
        monkeypatch.setattr(module,name,tmp_path/name)
    module.ACTIVE.write_bytes(b'old-test-binary')
    module.SOURCE.write_bytes(b'new-test-binary')
    module.READY.touch()
    monkeypatch.setattr(module,'OLD',hashlib.sha256(b'old-test-binary').hexdigest())
    monkeypatch.setattr(module,'NEW',hashlib.sha256(b'new-test-binary').hexdigest())
    monkeypatch.setattr(module.socket,'gethostname',lambda:'cs-s2s6-node2')
    monkeypatch.setattr(module.os,'geteuid',lambda:0,raising=False)
    starts=[]
    def run(args):
        if args[0]=='install': shutil.copyfile(module.SOURCE,module.PENDING)
        if args[:2]==['systemctl','is-active']: return 'active'
        if args[:2]==['systemctl','start']:
            starts.append(True)
            if len(starts)==1: raise RuntimeError('synthetic start failure')
        return ''
    monkeypatch.setattr(module,'run',run)
    with pytest.raises(RuntimeError,match='synthetic start failure'): module.main()
    assert module.ACTIVE.read_bytes()==b'old-test-binary'
    assert not module.BACKUP.exists()
    assert len(starts)==2
