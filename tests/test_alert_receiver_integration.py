"""Real Alertmanager bearer delivery to the production receiver, no real email."""
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import httpx
import pytest
import uvicorn
import yaml
from app.alert_receiver import create_alert_receiver


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def test_real_alertmanager_authenticates_firing_and_resolved(tmp_path):
    path=os.environ.get('TEST_ALERTMANAGER_PATH')
    if not path: pytest.skip('Explicit real Alertmanager binary required')
    assert Path(path).is_absolute() and Path(path).is_file()
    token='syntheticreceiveronly01234567890123456789'
    received=[]
    def send(notice): received.append(notice); return 'provider_accepted'
    receiver_port,manager_port=port(),port()
    server=uvicorn.Server(uvicorn.Config(create_alert_receiver(token,send),host='127.0.0.1',
        port=receiver_port,log_level='critical',access_log=False,proxy_headers=False))
    thread=threading.Thread(target=server.run,daemon=True)
    thread.start()
    token_file=tmp_path/'synthetic.token'; token_file.write_text(token)
    token_file.chmod(0o600)
    template=Path(__file__).resolve().parents[1]/'ops/prometheus/alertmanager-notifications.yml.template'
    config=yaml.safe_load(template.read_text())
    config['route'].update(group_wait='0s',group_interval='1s')
    hook=config['receivers'][0]['webhook_configs'][0]
    hook.update(url=f'http://127.0.0.1:{receiver_port}/alerts',timeout='1s')
    hook['http_config']['authorization']['credentials_file']=str(token_file)
    selected=tmp_path/'alertmanager.yml'; selected.write_text(yaml.safe_dump(config))
    process=subprocess.Popen([path,'--config.file='+str(selected),'--storage.path='+str(tmp_path/'state'),
        f'--web.listen-address=127.0.0.1:{manager_port}','--cluster.listen-address='],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(trust_env=False,timeout=2) as client:
            deadline=time.monotonic()+20
            while True:
                assert process.poll() is None,'Owned Alertmanager exited'
                try:
                    if client.get(f'http://127.0.0.1:{manager_port}/-/ready').status_code==200 and server.started: break
                except httpx.HTTPError: pass
                assert time.monotonic()<deadline,'Owned notification stack not ready'
                time.sleep(.1)
            assert client.get(f'http://127.0.0.1:{receiver_port}/health').status_code==403
            now=datetime.now(timezone.utc)
            labels={'alertname':'CustomerServiceModelBudgetRejected','instance':'synthetic-only'}
            def submit(end):
                response=client.post(f'http://127.0.0.1:{manager_port}/api/v2/alerts',json=[{
                    'labels':labels,'annotations':{'description':'synthetic-private-never-forward'},
                    'startsAt':(now-timedelta(seconds=5)).isoformat(),'endsAt':end.isoformat()}])
                assert response.status_code==200
            submit(now+timedelta(minutes=5))
            deadline=time.monotonic()+20
            while not any(notice.status=='firing' for notice in received):
                assert process.poll() is None and time.monotonic()<deadline,'No authenticated firing delivery'
                time.sleep(.1)
            submit(datetime.now(timezone.utc)-timedelta(seconds=1))
            deadline=time.monotonic()+30
            while not any(notice.status=='resolved' for notice in received):
                assert process.poll() is None and time.monotonic()<deadline,'No authenticated resolved delivery'
                time.sleep(.1)
            assert all('synthetic-private' not in notice.text() for notice in received)
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        server.should_exit=True
        thread.join(timeout=5)
        assert not thread.is_alive()
