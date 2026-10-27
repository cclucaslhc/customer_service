"""Cookie boundary against real isolated PostgreSQL; no model or real secrets."""
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app.auth_http import AuthPolicy
from app.main import create_app
from app.persistence.bundle import PostgreSQLRepositories
from app.security import SecretStore
from test_postgresql_integration import postgres_database


def test_cookie_login_csrf_revocation_and_legacy_barrier(postgres_database, tmp_path):
    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    tenant = bundle.identity.create_tenant("Synthetic HTTP")
    bundle.identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
    policy = AuthPolicy("https://localhost")
    application = create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path / "unused.key"), auth_policy=policy)
    payload = {"username": "synthetic.admin", "password": "synthetic-password-only", "tenant_id": tenant}
    with TestClient(application, base_url=policy.origin, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/auth/config").json() == {"enabled": True}
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json=payload).status_code == 403
        assert client.post("/api/auth/login", json=payload, headers={"Origin": "https://evil.invalid"}).status_code == 403
        malformed = client.post("/api/auth/login", json={**payload, "role": "admin"}, headers={"Origin": policy.origin})
        assert malformed.status_code == 400 and payload["password"] not in malformed.text
        response = client.post("/api/auth/login", json=payload, headers={"Origin": policy.origin})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie and "Domain=" not in cookie
        csrf = response.json()["csrf_token"]
        assert client.get("/api/auth/me").json()["principal"]["tenant_id"] == tenant
        assert client.get("/api/admin/providers").json()["providers"] == []
        assert client.get("/api/admin/documents").json() == []
        assert client.get("/api/chat/sessions").json() == []
        assert client.post("/api/auth/logout", headers={"Origin": policy.origin}).status_code == 403
        assert client.post("/api/auth/logout", headers=[(b"Origin", policy.origin.encode()), (b"X-CSRF-Token", b"\xff")]).status_code == 403
        token = client.cookies.get(policy.cookie_name)
        assert client.post("/api/auth/logout", headers={"Origin": policy.origin, "X-CSRF-Token": csrf}).status_code == 200
        client.cookies.set(policy.cookie_name, token)
        assert client.get("/api/auth/me").status_code == 401
        assert response.headers["cache-control"] == "no-store"
    assert not (tmp_path / "unused.key").exists()


def test_http_request_bound_management_isolation_roles_and_validation(postgres_database, tmp_path, monkeypatch):
    from app.providers import ModelGateway
    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    a, b = bundle.identity.create_tenant("Synthetic A"), bundle.identity.create_tenant("Synthetic B")
    for name, tenant, role in (("synthetic.a", a, "admin"), ("synthetic.b", b, "admin"), ("synthetic.customer", a, "customer")):
        bundle.identity.provision_user(name, "synthetic-password-only", tenant, role=role)
    def forbidden(*args, **kwargs):
        raise AssertionError("No real model HTTP client")
    monkeypatch.setattr(ModelGateway, "_client", forbidden)
    policy = AuthPolicy("https://localhost")
    application = create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path / "synthetic.key"), auth_policy=policy)
    with TestClient(application, base_url=policy.origin, client=("127.0.0.1", 50000)) as client:
        def login(name, tenant):
            client.cookies.clear()
            response = client.post("/api/auth/login", headers={"Origin": policy.origin}, json={
                "username": name, "password": "synthetic-password-only", "tenant_id": tenant})
            assert response.status_code == 200
            return {"Origin": policy.origin, "X-CSRF-Token": response.json()["csrf_token"]}
        headers = login("synthetic.a", a)
        setup = client.post("/api/admin/setup", headers=headers)
        assert setup.status_code == 200
        assert [p["provider"] for p in setup.json()["providers"]["providers"]] == ["deepseek", "openai", "local"]
        assert not setup.json()["providers"]["providers"][0]["configured"]
        saved = client.put("/api/admin/providers/deepseek", headers=headers, json={"model": "synthetic-a-model", "api_key": "synthetic-a-key-only"})
        assert saved.status_code == 200 and "synthetic-a-key-only" not in saved.text
        invalid = client.put("/api/admin/providers/deepseek", headers=headers, json={"model": [], "api_key": "synthetic-a-key-only"})
        assert invalid.status_code == 422 and "synthetic-a-key-only" not in invalid.text
        draft = client.post("/api/admin/prompts/drafts", headers=headers, json={"content": "Synthetic A-only policy", "change_note": "Synthetic"}).json()
        assert client.post(f"/api/admin/prompts/{draft['id']}/publish", headers=headers).status_code == 200
        token_a = client.cookies.get(policy.cookie_name)
        chat = bundle.chat.authenticated(token_a)
        import uuid
        sid = str(uuid.uuid4())
        chat.start_run(session_id=sid, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Synthetic A only", content="Synthetic",
            provider="deepseek", model="synthetic", prompt_version_id=draft["id"], now="2026-10-07T10:00:00+00:00")
        assert client.get("/api/admin/chat/sessions").json()["total"] == 1
        headers_b = login("synthetic.b", b)
        assert client.get("/api/admin/providers").json()["providers"] == []
        assert client.get("/api/admin/prompts").json()["versions"] == []
        assert client.get("/api/admin/chat/sessions").json()["total"] == 0
        assert client.get(f"/api/admin/chat/sessions/{sid}").status_code == 404
        assert client.post(f"/api/admin/prompts/{draft['id']}/publish", headers=headers_b).status_code == 404
        assert client.post("/api/admin/setup", headers=headers_b).json()["prompts"]["current"]["version"] == 1
        headers_c = login("synthetic.customer", a)
        for path in ("/api/admin/providers", "/api/admin/prompts", "/api/admin/chat/sessions", "/api/admin/contacts", "/api/admin/audit", "/api/admin/overview"):
            assert client.get(path).status_code == 403
        assert client.put("/api/admin/providers/deepseek", headers=headers_c, json={"model": "forged", "api_key": "synthetic-forged"}).status_code == 403
        assert client.post("/api/admin/setup", headers=headers_c).status_code == 403
        bundle.identity.logout(client.cookies.get(policy.cookie_name))
        assert client.get("/api/admin/providers").status_code == 401


def test_auth_policy_rejects_insecure_remote_or_implicit_storage():
    for origin in ("http://remote.invalid", "https://localhost/path", "https://user:password@localhost"):
        with pytest.raises(ValueError):
            AuthPolicy(origin)
    with pytest.raises(ValueError):
        AuthPolicy("http://remote.invalid", secure_cookie=False)
    assert AuthPolicy("http://127.0.0.1:8019", secure_cookie=False).cookie_name == "cs_local_session"
    with pytest.raises(ValueError, match="PostgreSQL"):
        create_app(auth_policy=AuthPolicy("https://localhost"))
