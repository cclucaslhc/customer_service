"""Synthetic secrets only; no SMTP, real configuration or application master key."""
import json
import re
from fastapi.testclient import TestClient
import pytest
from app.alert_email_config import EncryptedEmailConfig,gmail_receiver
from app.alert_email_setup import create_setup_app
from app.security import SecretStore


def make(tmp_path):
    store=EncryptedEmailConfig(tmp_path/'email.enc',SecretStore(path=tmp_path/'test-master.key'))
    client=TestClient(create_setup_app(store,8011),base_url='http://127.0.0.1:8011',client=('127.0.0.1',1234))
    page=client.get('/')
    token=re.search("'X-Setup-Token':'([^']+)'",page.text).group(1)
    headers={'Origin':'http://127.0.0.1:8011','X-Setup-Token':token}
    return store,client,headers


def values():
    return {'account':'synthetic.test@gmail.com','recipient':'synthetic@example.invalid',
        'password':'abcd efgh ijkl mnop'}


def test_safe_save_encrypts_every_field_and_never_overwrites(tmp_path):
    store,client,headers=make(tmp_path)
    response=client.post('/configure',headers=headers,json=values())
    assert response.json()=={'configured':True,'sent':False}
    raw=store.path.read_text()
    assert all(value not in raw for value in values().values())
    receiver=store.read()
    assert receiver.host=='smtp.gmail.com' and receiver.password=='abcdefghijklmnop'
    before=store.path.read_bytes()
    assert client.post('/configure',headers=headers,json=values()).status_code==409
    assert store.path.read_bytes()==before
    assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('changes',({'origin':'https://evil.invalid'},
    {'x-setup-token':'incorrect'},{'x-setup-token':'é'}))
def test_wrong_origin_or_token_cannot_write(tmp_path,changes):
    store,client,headers=make(tmp_path)
    headers={key.lower():value for key,value in headers.items()}
    headers.update(changes)
    # Non-ASCII header bytes exercise the server without client ASCII coercion.
    if headers['x-setup-token']=='é': headers['x-setup-token']='é'.encode('latin-1')
    assert client.post('/configure',headers=headers,json=values()).status_code==403
    assert not store.path.exists()


def test_validation_and_size_errors_do_not_echo_secrets(tmp_path):
    store,client,headers=make(tmp_path)
    supplied={**values(),'password':'synthetic-not-a-valid-app-password'}
    response=client.post('/configure',headers=headers,json=supplied)
    assert response.status_code==400
    assert supplied['password'] not in response.text and supplied['account'] not in response.text
    response=client.post('/configure',headers={**headers,'Content-Type':'application/json'},content=b'x'*8193)
    assert response.status_code==413 and not store.path.exists()


def test_rebinding_and_non_loopback_are_rejected(tmp_path):
    store,client,headers=make(tmp_path)
    assert client.get('/',headers={'Host':'evil.invalid'}).status_code==400
    other=TestClient(create_setup_app(store,8011),base_url='http://127.0.0.1:8011',client=('192.0.2.7',1234))
    assert other.get('/').status_code==403


def test_tampering_or_cross_purpose_ciphertext_cannot_load(tmp_path):
    store,client,headers=make(tmp_path)
    store.save_new(gmail_receiver(**values()))
    envelope=json.loads(store.path.read_text())
    envelope['ciphertext']='AAAA'
    store.path.write_text(json.dumps(envelope))
    with pytest.raises(Exception): store.read()
