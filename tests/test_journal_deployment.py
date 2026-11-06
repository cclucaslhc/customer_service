"""Deployment contracts; live namespace proof remains a separate Linux check."""
from pathlib import Path
import importlib.util
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_api_service_scopes_logs_and_does_not_load_historical_environment_keys():
    service=(ROOT/'ops/systemd/customer-service-api.service.template').read_text()
    assert 'LogNamespace=customer-service' in service
    assert 'User=customer-service-api' in service
    assert 'UMask=0077' in service
    assert '--json-http-logs' in service
    assert '--metrics-token-file ' in service
    assert 'EnvironmentFile=' not in service and 'API_KEY=' not in service
    assert 'StandardOutput=journal' in service
    assert 'CapabilityBoundingSet=\n' in service
    assert 'http://127.0.0.1:4318/v1/traces' in service


def test_retention_is_bounded_and_does_not_forward_to_global_sinks():
    config=(ROOT/'ops/systemd/journald-customer-service.conf').read_text()
    for directive in ('SplitMode=none','SystemMaxUse=512M','SystemKeepFree=1G','MaxRetentionSec=14day',
                      'MaxFileSec=1day','ForwardToSyslog=no','ForwardToKMsg=no',
                      'ForwardToConsole=no','Seal=yes'):
        assert directive in config


def test_lab_namespace_install_refuses_foreign_guest_before_writes(monkeypatch):
    spec=importlib.util.spec_from_file_location('linux_lab_journal',ROOT/'tests/fixtures/linux_lab_journal.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main()


def test_accelerated_retention_refuses_foreign_guest_before_creating_namespace(monkeypatch):
    spec=importlib.util.spec_from_file_location('journal_retention',ROOT/'tests/fixtures/linux_lab_journal_retention.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.socket,'gethostname',lambda:'unrelated')
    with pytest.raises(RuntimeError,match='Owned disposable node2'):
        module.main()


def test_accelerated_retention_uses_scoped_rotation_not_manual_vacuum():
    source=(ROOT/'tests/fixtures/linux_lab_journal_retention.py').read_text(encoding='utf-8')
    assert "'--namespace='+namespace" in source
    assert '--vacuum-' not in source
    assert "'--pipe'" not in source
    assert "'MaxRetentionSec=2s" in source
