import httpx
import pytest

from app.local_model_endpoint import LocalModelEndpoint


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://127.0.0.2:11434/v1",
    "http://[::1]:11434/v1",
    "https://127.0.0.1:11434/v1",
    "http://127.0.0.1/v1",
    "http://127.0.0.1:0/v1",
    "http://127.0.0.1:65536/v1",
    "http://user:pass@127.0.0.1:11434/v1",
    "http://127.0.0.1:11434/v1?target=private",
    "http://127.0.0.1:11434/v1#fragment",
    "http://127.0.0.1:11434/v1/../admin",
    "http://127.0.0.1:11434@external.test/v1",
    "http://127.0.0.1:11434/v1\\@external.test",
])
def test_rejects_noncanonical_or_nonloopback_endpoint(url):
    with pytest.raises(ValueError):
        LocalModelEndpoint(url)


def test_accepts_only_explicit_ipv4_loopback_v1():
    endpoint = LocalModelEndpoint("http://127.0.0.1:11434/v1")
    assert endpoint.base_url == "http://127.0.0.1:11434/v1"


@pytest.mark.asyncio
async def test_local_client_ignores_proxy_environment_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    endpoint = LocalModelEndpoint("http://127.0.0.1:11434/v1")
    async with endpoint.client() as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client.follow_redirects is False
        assert client.max_redirects == 0
        assert not client._trust_env
