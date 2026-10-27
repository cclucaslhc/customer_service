from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.auth_http import AuthPolicy
from app.main import create_app


def test_default_local_mode_still_rejects_remote_and_forged_forwarding():
    client=TestClient(create_app(),client=('203.0.113.25',51000))
    response=client.get('/api/auth/config',headers={'X-Forwarded-For':'127.0.0.1','X-Forwarded-Proto':'https'})
    assert response.status_code==403


def test_proxy_clients_cannot_enable_without_authentication():
    with pytest.raises(ValueError,match='secure authenticated strict API-role'):
        create_app(allow_authenticated_proxy_clients=True)


@pytest.mark.parametrize('strict,role,policy',[
    (False,'api',AuthPolicy('https://customer.example.test')),
    (True,'worker',AuthPolicy('https://customer.example.test')),
    (True,'api',AuthPolicy('http://127.0.0.1',secure_cookie=False)),
])
def test_proxy_clients_require_secure_api_role(strict,role,policy):
    repositories=SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        strict_role=strict,runtime_role=role)
    with pytest.raises(ValueError,match='secure authenticated strict API-role'):
        create_app(repositories=repositories,auth_policy=policy,allow_authenticated_proxy_clients=True)


def test_proxy_policy_does_not_accept_truthy_strings():
    with pytest.raises(ValueError,match='boolean'):
        create_app(allow_authenticated_proxy_clients='yes')
