"""Actual Prometheus parser/evaluator, explicitly provisioned tool only."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from app.backup_health import prometheus


@pytest.mark.parametrize('operation',('check','test'))
def test_prometheus_backup_rules_with_actual_promtool(operation):
    configured = os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('TEST_PROMTOOL_PATH unset; real Prometheus rule evaluation unverified')
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    root = Path(__file__).resolve().parents[1]
    target = root/'ops'/'prometheus'/('backup-alerts.yml' if operation == 'check' else 'backup-alerts.test.yml')
    result = subprocess.run([str(binary),operation,'rules',str(target)],cwd=root,
        capture_output=True,text=True,timeout=30)
    assert result.returncode == 0,result.stdout+result.stderr


def test_exported_metrics_with_actual_prometheus_parser():
    configured = os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('TEST_PROMTOOL_PATH unset; metrics parser unverified')
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    report = {'collected_at':1000,'policy_matches':True,'schedule_enabled':True,'healthy':False,
        'needs_review':0,'expired_running':0,'running':1,'invalid_reports':0,'history_truncated':False,
        'maintenance_hold':True,'recovery_hold':False,'max_age_seconds':3600,
        'require_remote':True,'require_restore':True,
        'evidence':{kind:{'snapshot_timestamp':900 if kind == 'local' else None,'fresh':kind == 'local'}
                    for kind in ('local','remote','restore')}}
    for content in (prometheus(report),prometheus()):
        result = subprocess.run([str(binary),'check','metrics'],input=content.encode('utf-8'),
            capture_output=True,timeout=30)
        assert result.returncode == 0,result.stdout+result.stderr


def test_actual_cli_failure_pipe_is_lf_and_parseable():
    configured = os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('TEST_PROMTOOL_PATH unset; actual CLI metrics pipe unverified')
    root = Path(__file__).resolve().parents[1]
    output = subprocess.run([sys.executable,str(root/'scripts'/'backup_health.py'),
        '--schedule-id','00000000-0000-0000-0000-000000000001','--policy-fingerprint','a'*64,
        '--max-age-seconds','3600','--format','prometheus'],capture_output=True,timeout=30)
    assert output.returncode == 1 and b'\r' not in output.stdout
    assert b'cs_backup_catalog_collection_success 0\n' in output.stdout
    parsed = subprocess.run([configured,'check','metrics'],input=output.stdout,capture_output=True,timeout=30)
    assert parsed.returncode == 0,parsed.stdout+parsed.stderr
