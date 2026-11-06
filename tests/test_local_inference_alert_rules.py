"""Real Prometheus expression evaluation; not an external on-call delivery proof."""
import os
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize('operation',['check','test'])
def test_local_inference_alerts_with_real_promtool(operation):
    configured=os.environ.get('TEST_PROMTOOL_PATH')
    if not configured: pytest.skip('TEST_PROMTOOL_PATH unset; local inference alerts not evaluated')
    binary=Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    root=Path(__file__).resolve().parents[1]
    file=root/'ops/prometheus'/('local-inference-alerts.yml' if operation=='check' else 'local-inference-alerts.test.yml')
    result=subprocess.run([str(binary),operation,'rules',str(file)],cwd=root,
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
