"""Real promtool parser and synthetic evaluation for the bounded log exporter."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('operation',('check','test'))
def test_http_log_rules_with_actual_promtool(operation):
    configured = os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('TEST_PROMTOOL_PATH unset; HTTP log alerts remain unverified')
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    root = Path(__file__).resolve().parents[1]
    target = root/'ops'/'prometheus'/('http-log-alerts.yml' if operation == 'check' else 'http-log-alerts.test.yml')
    result = subprocess.run([str(binary),operation,'rules',str(target)],cwd=root,
        capture_output=True,text=True,timeout=30)
    assert result.returncode == 0,result.stdout+result.stderr
