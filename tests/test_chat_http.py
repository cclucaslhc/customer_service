"""Identity-bound HTTP/Agent acceptance with actual PG and FunctionModel only."""
import uuid
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from pydantic_ai.messages import ToolReturnPart

from app.auth_http import AuthPolicy
from app.main import create_app
from app.knowledge import KnowledgeService
from app.persistence.bundle import PostgreSQLRepositories
from app.providers import ModelGateway, ProviderSnapshot
from app.security import SecretStore
from test_postgresql_integration import postgres_database


def test_authenticated_http_agent_rag_ownership_contacts_and_shared_capacity(postgres_database, tmp_path, monkeypatch):
    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=3)
    a, b = bundle.identity.create_tenant("Synthetic A"), bundle.identity.create_tenant("Synthetic B")
    paths = {"knowledge_dir": tmp_path / "docs", "asset_dir": tmp_path / "assets", "legacy_upload_dir": tmp_path / "legacy"}
    documents = {}
    for tenant, suffix in ((a, "a"), (b, "b")):
        name = f"synthetic.admin.{suffix}"
        bundle.identity.provision_user(name, "synthetic-password-only", tenant, role="admin")
        token = bundle.identity.login(name, "synthetic-password-only", tenant).token
        bundle.config.authenticated(token).initialize_tenant("2026-10-07T10:00:00+00:00")
        documents[tenant] = KnowledgeService(repository=bundle.knowledge.authenticated(token), **paths).ingest(
            "synthetic.txt", f"Synthetic lexical evidence only for tenant {suffix}.".encode())["id"]
    for name, tenant in (("synthetic.customer.a", a), ("synthetic.other.a", a), ("synthetic.customer.b", b)):
        bundle.identity.provision_user(name, "synthetic-password-only", tenant, role="customer")
    async def model_stream(messages, info):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            yield "Synthetic completed answer using tenant evidence."
        else:
            yield {0: DeltaToolCall(name="search_knowledge", json_args='{"query":"lexical"}', tool_call_id="synthetic-search")}
    def snapshot(gateway):
        return ProviderSnapshot("deepseek", "synthetic-function", "unused", gateway.repository.credential_namespace())
    def build(gateway, selected):
        assert selected.tenant_id == gateway.repository.credential_namespace()
        return FunctionModel(stream_function=model_stream)
    monkeypatch.setattr(ModelGateway, "snapshot", snapshot)
    monkeypatch.setattr(ModelGateway, "build_model", build)
    def forbidden(*args, **kwargs):
        raise AssertionError("Real model client forbidden")
    monkeypatch.setattr(ModelGateway, "_client", forbidden)
    policy = AuthPolicy("https://localhost")
    application = create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path / "unused.key"), knowledge_paths=paths, auth_policy=policy)
    with TestClient(application, base_url=policy.origin, client=("127.0.0.1", 50000)) as client:
        def login(name, tenant):
            client.cookies.clear()
            result = client.post("/api/auth/login", headers={"Origin": policy.origin}, json={"username": name,
                "password": "synthetic-password-only", "tenant_id": tenant})
            assert result.status_code == 200
            return {"Origin": policy.origin, "X-CSRF-Token": result.json()["csrf_token"]}
        ha = login("synthetic.customer.a", a)
        sid = client.post("/api/chat/sessions", headers=ha).json()["id"]
        answer = client.post(f"/api/chat/sessions/{sid}/messages", headers=ha, json={"content": "Synthetic lexical question"})
        assert "event: done" in answer.text and "event: citation" in answer.text
        assert documents[a] in answer.text and documents[b] not in answer.text
        history = client.get(f"/api/chat/sessions/{sid}").json()
        assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
        assert history["session"]["tenant_id"] == a
        message = history["messages"][1]["id"]
        assert client.put(f"/api/chat/sessions/{sid}/messages/{message}/feedback", headers=ha, json={"rating": "up"}).status_code == 200
        contact = client.post("/api/contacts", headers=ha, json={"session_id": sid, "name": "Synthetic", "email": "synthetic@example.com", "summary": "Synthetic"})
        assert contact.status_code == 200
        other = login("synthetic.other.a", a)
        assert client.get("/api/chat/sessions").json() == []
        assert client.get(f"/api/chat/sessions/{sid}").status_code == 404
        assert client.post(f"/api/chat/sessions/{sid}/messages", headers=other, json={"content": "forged"}).status_code == 404
        assert client.post(f"/api/chat/sessions/{sid}/runs/{uuid.uuid4()}/cancel", headers=other).json()["cancelled"] is False
        assert client.post("/api/contacts", headers=other, json={"session_id": sid, "name": "Synthetic", "email": "synthetic@example.com", "summary": "forged"}).status_code == 404
        hb = login("synthetic.customer.b", b)
        sid_b = client.post("/api/chat/sessions", headers=hb).json()["id"]
        answer_b = client.post(f"/api/chat/sessions/{sid_b}/messages", headers=hb, json={"content": "lexical"})
        assert documents[b] in answer_b.text and documents[a] not in answer_b.text
        assert len(client.get("/api/chat/sessions").json()) == 1
        assert application.state.chat.capacity()["active"] == application.state.chat.capacity()["waiting"] == 0
        assert application.state.chat.tokens == application.state.chat.runtime.finishers == {}
    assert not (tmp_path / "unused.key").exists()


@pytest.mark.asyncio
async def test_shared_runtime_cross_identity_cancel_capacity_and_revoked_stream(postgres_database, tmp_path):
    from app.chat import ChatOrchestrator, ChatRuntime
    from app.prompts import PromptService
    import sqlalchemy as sa
    from app.persistence.schema import metadata
    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    try:
        tenant = bundle.identity.create_tenant("Synthetic cancellation")
        tokens = {}
        for name, role in (("synthetic.admin", "admin"), ("synthetic.a", "customer"), ("synthetic.b", "customer")):
            bundle.identity.provision_user(name, "synthetic-password-only", tenant, role=role)
            tokens[name] = bundle.identity.login(name, "synthetic-password-only", tenant).token
        bundle.config.authenticated(tokens["synthetic.admin"]).initialize_tenant("2026-10-07T10:00:00+00:00")
        async def stream_model(messages, info):
            yield "Synthetic final response."
        class Gateway:
            def snapshot(self):
                return ProviderSnapshot("deepseek", "synthetic-function", "unused", tenant)
            def build_model(self, selected):
                return FunctionModel(stream_function=stream_model)
        shared = ChatRuntime(1)
        def view(name):
            token = tokens[name]
            return ChatOrchestrator(Gateway(), KnowledgeService(repository=bundle.knowledge.authenticated(token),
                knowledge_dir=tmp_path / "docs", asset_dir=tmp_path / "assets", legacy_upload_dir=tmp_path / "legacy"),
                repository=bundle.chat.authenticated(token), prompt_resolver=PromptService(bundle.config.authenticated(token)).runtime_prompt,
                runtime=shared, max_concurrent_runs=1, queue_timeout=0.03, identity_resolver=lambda: bundle.identity.resolve(token))
        a, b = view("synthetic.a"), view("synthetic.b")
        sid = str(uuid.uuid4())
        pending = a.stream(sid, "Synthetic active run")
        assert "run_started" in await anext(pending)
        rid = shared.session_runs[sid]
        assert a.capacity()["active"] == b.capacity()["active"] == 1
        assert b.cancel(sid, rid) is False and not shared.tokens[rid].cancelled
        rejected = [frame async for frame in b.stream(str(uuid.uuid4()), "Synthetic waiting")]
        assert "service_busy" in "".join(rejected)
        assert b.capacity()["active"] == 1 and b.capacity()["waiting"] == 0
        assert view("synthetic.a").cancel(sid, rid) is True
        finished = [frame async for frame in pending]
        assert "interrupted" in "".join(finished) and "event: done" not in "".join(finished)
        assert a.capacity()["active"] == 0 and shared.tokens == shared.finishers == {}
        revoked_sid = str(uuid.uuid4())
        revoked = a.stream(revoked_sid, "Synthetic revoked run")
        assert "run_started" in await anext(revoked)
        bundle.identity.logout(tokens["synthetic.a"])
        frames = [frame async for frame in revoked]
        assert "authentication_required" in "".join(frames) and "event: done" not in "".join(frames)
        assert shared.active == 0 and shared.tokens == shared.finishers == {}
        with bundle.engine.connect() as db:
            messages = metadata.tables["messages"]
            assert db.execute(sa.select(sa.func.count()).select_from(messages).where(messages.c.session_id == uuid.UUID(revoked_sid),
                messages.c.role == "assistant")).scalar_one() == 0
    finally:
        bundle.close()
