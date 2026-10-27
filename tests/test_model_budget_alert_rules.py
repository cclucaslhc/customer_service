"""Actual Prometheus parser/evaluator for private model-budget metrics."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('operation',('check','test'))
def test_model_budget_rules_with_actual_promtool(operation):
    configured=os.environ.get('TEST_PROMTOOL_PATH')
    if not configured:
        pytest.skip('TEST_PROMTOOL_PATH unset; model budget rules remain unverified')
    binary=Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    root=Path(__file__).resolve().parents[1]
    target=root/'ops'/'prometheus'/('model-budget-alerts.yml' if operation=='check' else 'model-budget-alerts.test.yml')
    result=subprocess.run([str(binary),operation,'rules',str(target)],cwd=root,
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
