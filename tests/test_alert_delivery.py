"""Opt-in real Prometheus -> Alertmanager -> loopback webhook delivery.

The live rule is copied with only its pending duration shortened for an
isolated integration run. This never contacts an operator notification channel.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import yaml


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _ready(url, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert process.poll() is None, 'Monitoring process exited before readiness'
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    pytest.fail('Monitoring process did not become ready')


def _wait_for(deliveries, status, processes):
    # Alertmanager may defer a resolved notification until its flush cycle.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'Monitoring process exited during delivery'
        if any(item == status for item in deliveries):
            return
        time.sleep(.2)
    if status in deliveries:
        return
    pytest.fail(f'Alertmanager did not deliver {status} notification')


@pytest.mark.parametrize('scenario',('http_log','model_circuit'))
def test_real_alertmanager_delivers_firing_and_resolved(tmp_path,scenario):
    binaries = [os.environ.get('TEST_PROMETHEUS_PATH'), os.environ.get('TEST_ALERTMANAGER_PATH')]
    if not all(binaries):
        pytest.skip('Real Prometheus and Alertmanager binaries are not configured')
    paths = [Path(value) for value in binaries]
    assert all(path.is_absolute() and path.is_file() and not path.is_symlink() for path in paths)
    prometheus_bin, alertmanager_bin = paths
    metrics_port, prometheus_port, alertmanager_port = _port(), _port(), _port()
    assert len({metrics_port, prometheus_port, alertmanager_port}) == 3
    deliveries = []
    alive = threading.Event()
    alert_name = ('CustomerServiceHttpLogPipelineUnavailable' if scenario == 'http_log'
                  else 'CustomerServiceModelCircuitProbeStuck')

    class SyntheticEndpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404)
                return
            payload = (f'customer_service_http_log_export_worker_alive {int(alive.is_set())}\n'
                       if scenario == 'http_log' else
                       f'customer_service_model_circuit_states{{state="half_open"}} {int(not alive.is_set())}\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.path != '/notify':
                self.send_error(404)
                return
            raw = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(raw)
            for item in payload.get('alerts', []):
                if item.get('labels', {}).get('alertname') == alert_name:
                    deliveries.append(item.get('status'))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    receiver = ThreadingHTTPServer(('127.0.0.1', metrics_port), SyntheticEndpoint)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[1]
    rule_path = ('http-log-alerts.yml' if scenario == 'http_log' else 'model-circuit-alerts.yml')
    rules = yaml.safe_load((root / 'ops/prometheus' / rule_path).read_text(encoding='utf-8'))
    rule = next(item for group in rules['groups'] for item in group['rules']
                if item.get('alert') == alert_name)
    assert rule['for'] == ('2m' if scenario == 'http_log' else '5m')
    rule['for'] = '0s'
    (tmp_path / 'rules.yml').write_text(yaml.safe_dump(rules), encoding='utf-8')
    (tmp_path / 'prometheus.yml').write_text(yaml.safe_dump({
        'global': {'scrape_interval': '1s', 'evaluation_interval': '1s'},
        'rule_files': [str(tmp_path / 'rules.yml')],
        'scrape_configs': [{'job_name': 'customer-service-private',
                            'static_configs': [{'targets': [f'127.0.0.1:{metrics_port}']}]}],
        'alerting': {'alertmanagers': [{'static_configs': [{'targets': [f'127.0.0.1:{alertmanager_port}']}]}]},
    }), encoding='utf-8')
    (tmp_path / 'alertmanager.yml').write_text(yaml.safe_dump({
        'route': {'receiver': 'synthetic', 'group_wait': '0s', 'group_interval': '1s',
                  'repeat_interval': '1m'},
        'receivers': [{'name': 'synthetic', 'webhook_configs': [
            {'url': f'http://127.0.0.1:{metrics_port}/notify', 'send_resolved': True}]}],
    }), encoding='utf-8')
    processes = []
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        for binary, arguments, name in (
            (alertmanager_bin, ['--config.file=' + str(tmp_path / 'alertmanager.yml'),
                                '--storage.path=' + str(tmp_path / 'alertmanager-data'),
                                f'--web.listen-address=127.0.0.1:{alertmanager_port}',
                                '--cluster.listen-address='], 'alertmanager'),
            (prometheus_bin, ['--config.file=' + str(tmp_path / 'prometheus.yml'),
                              '--storage.tsdb.path=' + str(tmp_path / 'prometheus-data'),
                              f'--web.listen-address=127.0.0.1:{prometheus_port}'], 'prometheus'),
        ):
            output = (tmp_path / f'{name}.log').open('wb')
            try:
                process = subprocess.Popen([str(binary), *arguments], cwd=tmp_path,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
            finally:
                output.close()
            processes.append(process)
        _ready(f'http://127.0.0.1:{alertmanager_port}/-/ready', processes[0])
        _ready(f'http://127.0.0.1:{prometheus_port}/-/ready', processes[1])
        _wait_for(deliveries, 'firing', processes)
        alive.set()
        _wait_for(deliveries, 'resolved', processes)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=3)
