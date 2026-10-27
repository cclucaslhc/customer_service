"""Configuration transactions use synthetic local databases, never real keys."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
import sqlalchemy as sa
import httpx
from pydantic_ai.models import ModelRequestParameters

import app.database as database
from app.persistence.config_repository import ConfigurationRepository
from app.providers import ModelGateway, ProviderError, ProviderSnapshot
from app.local_model_endpoint import LocalModelEndpoint


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / "configuration.sqlite"
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    monkeypatch.setattr(database, "ensure_directories", lambda: None)
    database.init_database()
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(path)), hide_parameters=True,
                              connect_args={"check_same_thread": False, "timeout": 10})
    yield ConfigurationRepository(engine)
    engine.dispose()


def test_provider_key_update_and_activation(repository):
    now = database.utc_now()
    assert not repository.activate_provider("openai")
    assert repository.update_provider("deepseek", model="synthetic-model", now=now, encrypted_key=b"synthetic", nonce=b"nonce", replace_key=True)
    assert repository.activate_provider("deepseek")
    assert repository.update_provider("deepseek", model="other-model", now=now)
    row = repository.active_provider()
    assert row["model"] == "other-model" and row["encrypted_key"] == b"synthetic"
    with pytest.raises(ValueError):
        repository.update_provider("deepseek", model="bad", now=now, encrypted_key=b"incomplete", replace_key=True)
    assert repository.delete_provider_key("deepseek", now)
    assert repository.active_provider()["encrypted_key"] is None
    assert repository.active_provider()["nonce"] is None
    assert not repository.activate_provider("deepseek")


def test_gateway_public_config_and_snapshot_repr_never_expose_key(repository):
    repository.update_provider("deepseek", model="test", now=database.utc_now(), encrypted_key=b"synthetic", nonce=b"nonce", replace_key=True)
    class NoDecrypt:
        def decrypt(self, *args):
            raise AssertionError("Listing must not decrypt keys")
    public = ModelGateway(NoDecrypt(), repository=repository).list_configs()
    assert public["active_provider"] == "deepseek"
    assert all("encrypted_key" not in row and "nonce" not in row for row in public["providers"])
    assert "never-log-this-synthetic-value" not in repr(ProviderSnapshot("deepseek", "test", "never-log-this-synthetic-value"))


def test_local_activation_without_host_endpoint_fails_without_switching_default(repository):
    gateway = ModelGateway(object(), repository=repository)
    assert {row["provider"] for row in gateway.list_configs()["providers"]} == {"deepseek", "openai", "local"}
    with pytest.raises(ProviderError) as error:
        gateway.activate("local")
    assert error.value.code == "local_endpoint_unavailable"
    assert repository.provider_configs()[0] == "deepseek"


def test_standby_snapshot_is_tenant_scoped_and_does_not_activate_provider(repository):
    class SyntheticSecrets:
        def decrypt(self, encrypted, nonce, provider, *, tenant_id=None):
            assert (encrypted, nonce, provider, tenant_id) == (
                b"synthetic", b"nonce", "openai", None)
            return "synthetic-test-key"

    gateway = ModelGateway(SyntheticSecrets(), repository=repository,
        local_endpoint=LocalModelEndpoint("http://127.0.0.1:11434/v1"))
    with pytest.raises(ProviderError) as missing:
        gateway.fallback_snapshot("openai")
    assert missing.value.code == "provider_not_configured"
    with pytest.raises(ProviderError) as unsupported:
        gateway.fallback_snapshot("deepseek")
    assert unsupported.value.code == "unsupported_provider"
    repository.update_provider("openai", model="synthetic-online", now=database.utc_now(),
        encrypted_key=b"synthetic", nonce=b"nonce", replace_key=True)
    repository.update_provider("local", model="synthetic-local", now=database.utc_now())
    online = gateway.fallback_snapshot("openai")
    local = gateway.fallback_snapshot("local")
    assert (online.provider, online.model, online.api_key) == (
        "openai", "synthetic-online", "synthetic-test-key")
    assert (local.provider, local.model) == ("local", "synthetic-local")
    assert repository.provider_configs()[0] == "deepseek"


@pytest.mark.asyncio
async def test_online_connection_check_is_non_generative_and_validates_directory(repository):
    seen = []
    replies = [httpx.Response(200, json={"object":"list", "data":[{"id":"synthetic-model"}]}),
               httpx.Response(302, headers={"Location":"https://example.invalid/models"}),
               httpx.Response(200, text="not-json")]
    def handler(request):
        seen.append(request)
        return replies.pop(0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ModelGateway(object(), repository=repository, client_factory=lambda: client)
    try:
        result = await gateway.test("deepseek", "synthetic-model", "synthetic-test-key")
        assert result == {"status":"success", "message":"API 连接正常且模型目录包含所选模型（生成及工具能力未验证）",
                          "model":"synthetic-model"}
        assert (await gateway.test("deepseek", None, "synthetic-test-key"))["status"] == "error"
        assert (await gateway.test("openai", None, "synthetic-test-key"))["status"] == "error"
        assert [(request.method, request.url.path) for request in seen] == [
            ("GET", "/models"), ("GET", "/models"), ("GET", "/v1/models")]
        assert all(request.content == b"" and request.headers["Authorization"] == "Bearer synthetic-test-key"
                   for request in seen)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_online_connection_check_requires_selected_model_id(repository):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"object":"list", "data":[{"id":"other-model"}]})))
    gateway = ModelGateway(object(), repository=repository, client_factory=lambda: client)
    try:
        result = await gateway.test('deepseek', 'synthetic-model', 'synthetic-test-key')
        assert result == {'status':'error', 'message':'供应商模型目录未列出所选模型',
                          'model':'synthetic-model'}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_online_connection_check_rejects_oversized_directory(repository):
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, content=b' ' * 262145)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ModelGateway(object(), repository=repository, client_factory=lambda: client)
    try:
        result = await gateway.test('deepseek', 'synthetic-model', 'synthetic-test-key')
        assert result['status'] == 'error'
        assert result['message'] == '供应商模型目录响应无效'
        assert seen == [('GET', '/models')]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_fallback_tool_probe_uses_only_synthetic_prompt(repository):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
            "type": "function", "function": {"name": "cs_capability_probe",
            "arguments": '{"ok":true}'}}]}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ModelGateway(object(), repository=repository,
        local_endpoint=LocalModelEndpoint('http://127.0.0.1:11434/v1'),
        local_client_factory=lambda: client)
    try:
        await gateway.ensure_local_tool_capability(ProviderSnapshot('local', 'synthetic-local', 'local-only'))
        assert len(requests) == 1
        assert requests[0].url.host == '127.0.0.1'
        assert requests[0].url.path == '/v1/chat/completions'
        payload = json.loads(requests[0].content)
        assert payload['model'] == 'synthetic-local'
        assert payload['messages'] == [{'role':'user', 'content':'Call cs_capability_probe with ok=true.'}]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', [
    {"choices": [{"message": {"content": "yes"}}]},
    {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "wrong_tool", "arguments": '{"ok":true}'}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "cs_capability_probe", "arguments": '{"ok":false}'}}]}}]},
])
async def test_local_fallback_tool_probe_fails_closed(repository, reply):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=reply)))
    gateway = ModelGateway(object(), repository=repository,
        local_endpoint=LocalModelEndpoint('http://127.0.0.1:11434/v1'),
        local_client_factory=lambda: client)
    try:
        with pytest.raises(ProviderError) as error:
            await gateway.ensure_local_tool_capability(ProviderSnapshot('local', 'synthetic-local', 'local-only'))
        assert error.value.code == 'local_capability_unverified'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_provider_real_loopback_http_protocol(repository):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("GET", self.path, self.headers.get("Authorization")))
            payload = b'{"object":"list","data":[{"id":"local-test","object":"model"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", self.path, body.get("model")))
            payload = json.dumps({"id": "synthetic", "object": "chat.completion",
                "created": 1, "model": "local-test", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "loopback-ok"},
                    "finish_reason": "stop"}], "usage": {"prompt_tokens": 2,
                    "completion_tokens": 2, "total_tokens": 4}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = ModelGateway(object(), repository=repository,
        local_endpoint=LocalModelEndpoint(f"http://127.0.0.1:{server.server_port}/v1"))
    try:
        gateway.update("local", "local-test", None)
        assert (await gateway.test("local", None, None))["status"] == "success"
        gateway.activate("local")
        response = await gateway.build_model(gateway.snapshot()).request([], None,
            ModelRequestParameters())
        assert response.parts[0].content == "loopback-ok"
        with pytest.raises(ProviderError) as probe_error:
            await gateway.ensure_local_tool_capability(gateway.snapshot())
        assert probe_error.value.code == 'local_capability_unverified'
        assert requests[0][:2] == ("GET", "/v1/models")
        assert requests[1] == ("POST", "/v1/chat/completions", "local-test")
        assert requests[2] == ("POST", "/v1/chat/completions", "local-test")
    finally:
        await gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_local_connection_check_requires_selected_model_and_bounded_directory(repository):
    replies = [
        httpx.Response(200, json={"data": [{"id": "other-model"}]}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, content=b"x" * 262145),
        httpx.Response(200, json={"data": [{"id": "local-test"}]}),
    ]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: replies.pop(0)),
                               trust_env=False, follow_redirects=False)
    gateway = ModelGateway(object(), repository=repository,
        local_endpoint=LocalModelEndpoint("http://127.0.0.1:11434/v1"),
        local_client_factory=lambda: client)
    gateway.update("local", "local-test", None)
    try:
        for _ in range(3):
            assert (await gateway.test("local", None, None))["status"] == "error"
        result = await gateway.test("local", None, None)
        assert result == {"status": "success", "model": "local-test",
                          "message": "本机已列出所选模型（生成及工具能力未验证）"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_provider_uses_only_injected_loopback_client_and_no_saved_key(repository):
    class NoSecrets:
        def encrypt(self, *_args, **_kwargs):
            raise AssertionError("Local model must not encrypt an API key")
        def decrypt(self, *_args, **_kwargs):
            raise AssertionError("Local model must not decrypt an API key")
    seen = []
    def transport(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion",
            "created": 1, "model": "local-test", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False,
                               follow_redirects=False)
    gateway = ModelGateway(NoSecrets(), repository=repository,
        local_endpoint=LocalModelEndpoint("http://127.0.0.1:11434/v1"),
        local_client_factory=lambda: client)
    try:
        gateway.update("local", "local-test", None)
        assert gateway.activate("local") is None
        snapshot = gateway.snapshot()
        assert snapshot.provider == "local" and snapshot.model == "local-test"
        assert repository.active_provider()["encrypted_key"] is None
        model = gateway.build_model(snapshot)
        response = await model.request([], None, ModelRequestParameters())
        assert response.parts[0].content == "ok"
        assert len(seen) == 1 and seen[0].url.host == "127.0.0.1"
        assert seen[0].url.port == 11434 and seen[0].url.path == "/v1/chat/completions"
        with pytest.raises(Exception, match="本机模型不接受 API Key"):
            gateway.update("local", "local-test", "synthetic-key")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_provider_does_not_follow_model_server_redirect(repository):
    seen = []
    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://external.test/steal"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False,
                               follow_redirects=False)
    gateway = ModelGateway(object(), repository=repository,
        local_endpoint=LocalModelEndpoint("http://127.0.0.1:11434/v1"),
        local_client_factory=lambda: client)
    try:
        model = gateway.build_model(ProviderSnapshot("local", "local-test", "local-only"))
        with pytest.raises(Exception):
            await model.request([], None, ModelRequestParameters())
        assert len(seen) == 1 and seen[0].startswith("http://127.0.0.1:11434/v1/")
    finally:
        await client.aclose()


def test_concurrent_prompt_drafts_and_publications(repository):
    initial = len(repository.list_prompts())
    def create(index):
        return repository.create_draft(content=f"synthetic {index}", change_note="test", now=database.utc_now())
    with ThreadPoolExecutor(max_workers=4) as executor:
        drafts = list(executor.map(create, range(12)))
    assert len({item["id"] for item in drafts}) == 12
    assert len({item["version"] for item in drafts}) == 12
    assert len(repository.list_prompts()) == initial + 12
    with ThreadPoolExecutor(max_workers=4) as executor:
        published = list(executor.map(lambda row: repository.publish_prompt(row["id"], database.utc_now()), drafts))
    assert all(row["status"] == "published" for row in published)
    assert sum(row["status"] == "published" for row in repository.list_prompts()) == 1


def test_rollback_creates_new_version_atomically(repository, monkeypatch):
    original = repository.active_prompt()
    next_prompt = repository.create_draft(content="different", change_note="test", now=database.utc_now())
    repository.publish_prompt(next_prompt["id"], database.utc_now())
    before = repository.list_prompts()
    def fail_publish(db, prompt_id, now):
        p = repository.tables["prompt_versions"]
        db.execute(p.update().where(p.c.status == "published").values(status="archived"))
        raise RuntimeError("synthetic publish failure")
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_publish", fail_publish)
        with pytest.raises(RuntimeError):
            repository.rollback_prompt(original["id"], "", database.utc_now())
    assert repository.list_prompts() == before  # Neither draft nor archiving survives.
    restored = repository.rollback_prompt(original["id"], "", database.utc_now())
    assert restored["id"] != original["id"]
    assert restored["content"] == original["content"]
    assert restored["version"] > next_prompt["version"]
    assert repository.active_prompt()["id"] == restored["id"]
    before = repository.list_prompts()
    assert repository.publish_prompt("missing", database.utc_now()) is None
    assert repository.rollback_prompt("missing", "", database.utc_now()) is None
    assert repository.list_prompts() == before
