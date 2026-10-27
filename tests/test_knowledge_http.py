"""HTTP identity isolation for documents/assets; synthetic in-memory uploads."""
import io
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image

from app.auth_http import AuthPolicy
from app.main import create_app
from app.persistence.bundle import PostgreSQLRepositories
from app.security import SecretStore
from test_postgresql_integration import postgres_database


def test_bound_knowledge_http_upload_assets_terms_roles_and_revocation(postgres_database, tmp_path, monkeypatch):
    import app.knowledge as knowledge_module
    from app.knowledge_models import EmbeddedAsset
    original_parser = knowledge_module.parse_structured_document
    png_buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(png_buffer, format="PNG")
    png = png_buffer.getvalue()
    calls = []
    def synthetic_parser(*args, **kwargs):
        calls.append(True)
        doc = original_parser(*args, **kwargs)
        doc.assets = [EmbeddedAsset(0, png, "image/png", ".png", "Synthetic", "Synthetic", 1, 1)]
        return doc
    monkeypatch.setattr(knowledge_module, "parse_structured_document", synthetic_parser)
    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=2)
    a, b = bundle.identity.create_tenant("Synthetic A"), bundle.identity.create_tenant("Synthetic B")
    for name, tenant, role in (("synthetic.a", a, "admin"), ("synthetic.b", b, "admin"), ("synthetic.customer", a, "customer")):
        bundle.identity.provision_user(name, "synthetic-password-only", tenant, role=role)
    policy = AuthPolicy("https://localhost")
    application = create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path / "unused.key"), auth_policy=policy,
        knowledge_paths={"knowledge_dir": tmp_path / "docs", "asset_dir": tmp_path / "assets", "legacy_upload_dir": tmp_path / "legacy"})
    with TestClient(application, base_url=policy.origin, client=("127.0.0.1", 50000)) as client:
        def login(name, tenant):
            client.cookies.clear()
            response = client.post("/api/auth/login", headers={"Origin": policy.origin}, json={"username": name,
                "password": "synthetic-password-only", "tenant_id": tenant})
            assert response.status_code == 200
            return {"Origin": policy.origin, "X-CSRF-Token": response.json()["csrf_token"]}
        ha = login("synthetic.a", a)
        uploaded = client.post("/api/admin/documents", headers=ha, files={"file": ("synthetic.txt", b"Synthetic lexical evidence for tenant-specific testing.", "text/plain")})
        assert uploaded.status_code == 200
        da = uploaded.json()["id"]
        detail = client.get(f"/api/admin/documents/{da}").json()
        aa = detail["assets"][0]["id"]
        assert detail["document"]["tenant_id"] == a
        assert client.post("/api/admin/knowledge/search-test", headers=ha, json={"query": "lexical"}).json()["items"][0]["document_id"] == da
        assert client.put("/api/admin/knowledge/terms", headers=ha, json={"groups": [["synthetic", "example"]]}).status_code == 200
        assert client.post("/api/admin/index/rebuild", headers=ha).status_code == 200
        hb = login("synthetic.b", b)
        assert client.get("/api/admin/documents").json() == []
        assert client.get(f"/api/admin/documents/{da}").status_code == 404
        assert client.get(f"/api/knowledge/assets/{aa}").status_code == 404
        assert client.get("/api/admin/knowledge/terms").json()["custom"] == []
        assert client.post("/api/admin/knowledge/search-test", headers=hb, json={"query": "lexical"}).json()["items"] == []
        assert client.post("/api/admin/knowledge/import", headers=hb, json={"path": str(tmp_path / "docs" / a)}).status_code == 400
        second = client.post("/api/admin/documents", headers=hb, files={"file": ("synthetic.txt", b"Synthetic lexical evidence for tenant-specific testing.", "text/plain")})
        assert second.status_code == 200 and second.json()["id"] != da
        ab = client.get(f"/api/admin/documents/{second.json()['id']}").json()["assets"][0]["id"]
        hc = login("synthetic.customer", a)
        assert client.get(f"/api/knowledge/assets/{aa}").content == png
        assert client.get(f"/api/knowledge/assets/{aa}").headers["cache-control"] == "no-store"
        assert client.get(f"/api/knowledge/assets/{ab}").status_code == 404
        assert client.get("/api/admin/documents").status_code == 403
        assert client.post("/api/admin/documents", headers=hc, files={"file": ("denied.txt", b"Denied synthetic", "text/plain")}).status_code == 403
        assert client.post(f"/api/admin/documents/{da}/reparse", headers=hc).status_code == 403
        assert client.post("/api/admin/index/rebuild", headers=hc).status_code == 403
        assert len(calls) == 2  # Role/path denial happens before parsing/OCR.
        bundle.identity.logout(client.cookies.get(policy.cookie_name))
        assert client.get(f"/api/knowledge/assets/{aa}").status_code == 401
    assert not (tmp_path / "unused.key").exists()
