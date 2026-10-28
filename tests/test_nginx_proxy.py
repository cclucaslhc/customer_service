"""Opt-in real Linux proxy tests of the repository template, not full app HA."""
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


PYTHON_IMAGE = 'python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2'


def docker(*args):
    result = subprocess.run(['docker', *map(str, args)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return (result.stdout+result.stderr if args[0] == 'logs' else result.stdout).strip()


@pytest.fixture(scope='module')
def proxy(tmp_path_factory):
    image = os.environ.get('TEST_NGINX_IMAGE')
    if not image:
        pytest.skip('TEST_NGINX_IMAGE must explicitly select a digest-pinned isolated NGINX image')
    if '@sha256:' not in image or len(image.rsplit('@sha256:', 1)[1]) != 64:
        pytest.fail('NGINX test image must be pinned by digest')
    root = tmp_path_factory.mktemp('nginx-synthetic')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()))
    (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    repository = Path(__file__).resolve().parents[1]
    template = (repository/'ops/nginx/customer-service.conf.template').read_text(encoding='utf-8')
    for name, value in {'CANONICAL_HOST':'localhost', 'TLS_CERTIFICATE_PATH':'/probe/cert.pem',
                       'TLS_PRIVATE_KEY_PATH':'/probe/key.pem', 'APPROVED_UPLOAD_LIMIT':'1m'}.items():
        template = template.replace('@'+name+'@', value)
    assert all('@' not in line for line in template.splitlines() if not line.lstrip().startswith('#'))
    config = ('pid /tmp/nginx.pid; error_log stderr warn; events { worker_connections 128; }\n'
              'http { access_log off; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy; '
              'fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi;\n'+template+'\n}')
    (root/'nginx.conf').write_text(config, encoding='utf-8')
    backend, edge = 'cs-proxy-peer-'+uuid.uuid4().hex, 'cs-proxy-edge-'+uuid.uuid4().hex
    created = []
    try:
        docker('run', '-d', '--name', backend, '--read-only', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--memory=128m', '--pids-limit=32',
               '--publish', '127.0.0.1::443', '--mount',
               f'type=bind,source={repository / "tests/fixtures/proxy_probe.py"},target=/probe.py,readonly',
               PYTHON_IMAGE, 'python', '-B', '/probe.py')
        created.append(backend)
        docker('run', '-d', '--name', edge, '--network', 'container:'+backend,
               '--read-only', '--tmpfs', '/tmp:rw,size=16m', '--memory=128m', '--pids-limit=64',
               '--mount', f'type=bind,source={root},target=/probe,readonly',
               '--entrypoint', 'nginx', image, '-c', '/probe/nginx.conf', '-g', 'daemon off;')
        created.append(edge)
        try:
            docker('exec', edge, 'nginx', '-t', '-c', '/probe/nginx.conf')
        except RuntimeError as exc:
            raise RuntimeError(docker('logs', edge)) from exc
        port = int(docker('port', backend, '443/tcp').rsplit(':', 1)[1])
        context = ssl.create_default_context(cafile=str(root/'cert.pem'))
        deadline = time.monotonic()+10
        while True:
            connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=5)
            try:
                connection.request('GET', '/ready')
                response = connection.getresponse()
                status=response.status
                response.read()
                if status==200: break
                # The proxy may be listening before its synthetic backend is
                # ready on a loaded runner. Only readiness 502/503 is transient;
                # persistent errors still fail at the original bounded deadline.
                assert status in (502,503),'Unexpected readiness response'
                assert time.monotonic()<deadline,'Proxy backend never became ready'
                time.sleep(.1)
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
            finally:
                connection.close()
        gateway = docker('inspect', backend, '--format', '{{.NetworkSettings.Networks.bridge.Gateway}}')
        yield port, context, gateway
    finally:
        for name in reversed(created):
            docker('rm', '-f', name)


def test_proxy_tls_and_forwarding_headers(proxy):
    port, context, gateway = proxy
    untrusted = http.client.HTTPSConnection('127.0.0.1', port, timeout=5)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            untrusted.request('GET', '/')
    finally:
        untrusted.close()
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=5)
    try:
        connection.request('GET', '/', headers={'Host':'localhost', 'X-Forwarded-Proto':'http',
            'X-Forwarded-For':'198.51.100.77', 'Last-Event-ID':'synthetic-cursor'})
        response = connection.getresponse()
        assert response.status == 200
        payload = json.loads(response.read())
        assert payload.pop('forwarded') in {gateway, '127.0.0.1'}
        assert payload == {'host':'localhost', 'proto':'https', 'last_event_id':'synthetic-cursor'}
    finally:
        connection.close()


def test_proxy_sse_is_not_buffered(proxy):
    port, context, _ = proxy
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=5)
    try:
        started = time.monotonic()
        connection.request('GET', '/stream', headers={'Host':'localhost'})
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader('Content-Type') == 'text/event-stream'
        assert response.getheader('X-Accel-Buffering') == 'no'
        assert response.readline() == b'id: 1\n'
        assert response.readline() == b'data: first\n'
        assert time.monotonic()-started < 1.5
        assert response.read() == b'\nid: 2\ndata: done\n\n'
        assert time.monotonic()-started >= 1.8
    finally:
        connection.close()


def test_proxy_preserves_202_and_enforces_upload_limit(proxy):
    port, context, _ = proxy
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=5)
    try:
        connection.request('POST', '/enqueue', body=b'{}', headers={'Host':'localhost',
            'Content-Type':'application/json'})
        response = connection.getresponse()
        assert response.status == 202
        assert json.loads(response.read()) == {'status':'pending', 'synthetic':True}
        # Send only the declared length: NGINX must reject before reading a
        # large body or forwarding it to the synthetic backend.
        connection.putrequest('POST', '/enqueue', skip_host=True)
        connection.putheader('Host', 'localhost')
        connection.putheader('Content-Length', str(1024*1024+1))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        assert 'nginx/' not in response.getheader('Server', '')
        response.read()
    finally:
        connection.close()
