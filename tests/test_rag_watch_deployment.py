"""Deployment contract only; actual Linux supervisor/alert acceptance is separate."""
from configparser import ConfigParser
from pathlib import Path


def test_health_supervisor_cannot_restart_or_silence_pause_and_errors():
    path=Path(__file__).resolve().parents[1]/'ops/systemd/customer-service-rag-watch.service'
    unit=ConfigParser(interpolation=None)
    unit.read(path,encoding='utf-8')
    service=unit['Service']
    assert service['User']=='customer-service-control'
    assert service['Restart']=='no'
    assert 'SuccessExitStatus' not in service
    assert service['RuntimeMaxSec']=='7d'
    assert '--database-url-file ' in service['ExecStart']
    assert '--database-url ' not in service['ExecStart']
    assert '--control-file ' in service['ExecStart']
    assert service['ProtectSystem']=='strict' and service['NoNewPrivileges']=='yes'
    assert service['CapabilityBoundingSet']==''
    assert unit['Unit']['OnFailure']=='customer-service-ops-alert@%n.service'
