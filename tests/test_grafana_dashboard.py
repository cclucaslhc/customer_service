"""Dashboard contract; not a replacement for live Grafana query/render checks."""
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_dashboard_shared_aggregates_are_not_double_counted_or_zero_filled():
    dashboard=json.loads((ROOT/'ops/grafana/customer-service-s2.json').read_text(encoding='utf-8'))
    variables={item['name']:item for item in dashboard['templating']['list']}
    assert not variables['aggregate_instance']['multi']
    assert not variables['aggregate_instance']['includeAll']
    assert dashboard['timezone']=='utc' and not dashboard['editable']
    assert len({panel['id'] for panel in dashboard['panels']})==len(dashboard['panels'])
    budget=next(panel for panel in dashboard['panels'] if panel['id']==3)
    assert budget['fieldConfig']['defaults']['thresholds']['steps']==[
        {'color':'gray','value':None},{'color':'red','value':0},{'color':'green','value':1}]
    for panel in dashboard['panels']:
        assert 'NO DATA' in panel['fieldConfig']['defaults']['noValue']
        for target in panel['targets']:
            expression=target['expr']
            assert 'job="customer-service-private"' in expression
            assert 'vector(0)' not in expression
            if 'customer_service_model_' in expression:
                assert 'instance="$aggregate_instance"' in expression
                assert 'sum(' not in expression


def test_dashboard_queries_use_current_exporter_names_without_sensitive_variables():
    dashboard=json.loads((ROOT/'ops/grafana/customer-service-s2.json').read_text(encoding='utf-8'))
    sources='\n'.join((ROOT/path).read_text(encoding='utf-8') for path in (
        'app/http_observability.py','app/model_budget_metrics.py'))
    for panel in dashboard['panels']:
        for target in panel['targets']:
            import re
            for metric in re.findall(r'customer_service_[a-z_]+',target['expr']):
                assert metric.removesuffix('_bucket') in sources
    assert {item['name'] for item in dashboard['templating']['list']}=={'datasource','aggregate_instance'}


def test_acceptance_images_are_pinned_and_cli_errors_do_not_echo_output(monkeypatch):
    spec=importlib.util.spec_from_file_location('verify_grafana',ROOT/'scripts/verify_grafana.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert '@sha256:' in module.PROMETHEUS
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(
        returncode=1,stdout='synthetic-sensitive',stderr='synthetic-sensitive'))
    with pytest.raises(RuntimeError) as caught:
        module.docker('run','--env','synthetic-sensitive')
    assert 'sensitive' not in str(caught.value)


def test_acceptance_requires_explicit_application_image():
    import subprocess
    import sys
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_grafana.py')],
        capture_output=True,text=True,timeout=10)
    assert result.returncode==2
    assert '--app-image' in result.stderr
    source=(ROOT/'scripts/verify_grafana.py').read_text(encoding='utf-8')
    assert "APP='sha256:" not in source


def test_prometheus_only_mounts_synthetic_files_not_private_mkdtemp_directory(tmp_path):
    spec=importlib.util.spec_from_file_location('verify_grafana_mounts',ROOT/'scripts/verify_grafana.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tmp_path.chmod(0o700)
    arguments=module.prometheus_mounts(tmp_path)
    assert arguments==[
        '--mount',f'type=bind,source={tmp_path/"prometheus.yml"},target=/etc/prometheus/prometheus.yml,readonly',
        '--mount',f'type=bind,source={tmp_path/"metrics-token"},target=/etc/prometheus/metrics-token,readonly',
    ]
    source=(ROOT/'scripts/verify_grafana.py').read_text(encoding='utf-8')
    assert 'credentials_file: /etc/prometheus/metrics-token' in source
    assert 'target=/evidence' not in source


def test_grafana_artifact_pins_plugin_and_disables_runtime_downloads():
    source=(ROOT/'ops/grafana/Dockerfile').read_text(encoding='utf-8')
    assert 'GF_PLUGINS_PREINSTALL_DISABLED=true' in source
    assert 'GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false' in source
    assert 'GF_PATHS_PLUGINS=/opt/customer-service-plugins' in source
    assert 'chmod 0555 /plugins/prometheus/gpx_grafana-prometheus-datasource_linux_amd64' in source
    assert 'ADD --checksum=sha256:d24df3eaff9ae61f00d7e857f508fc306912dcf50d657150d1e472e851799fc4 https://grafana.com/api/plugins/prometheus/versions/13.2.5/download?os=linux&arch=amd64' in source
    assert source.rstrip().endswith('USER 472')
    assert 'go get github.com/go-jose/go-jose/v4@v4.1.6 && go mod verify' in source
    assert 'GOSUMDB=sum.golang.org' in source
    assert 'COPY --from=server /out/grafana /usr/share/grafana/bin/grafana' in source
