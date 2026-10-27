"""Synthetic static root only; no application DB, real uploads or credentials."""
from fastapi.testclient import TestClient
import app.main as main_module


def test_frontend_html_uncached_and_path_cannot_escape(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Synthetic SPA</h1>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("Synthetic outside root", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", dist)
    client = TestClient(main_module.create_app())  # No lifespan/database access.
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/admin/models").headers["cache-control"] == "no-store"
    assert client.get("/..%2Foutside.txt").status_code == 404
    assert client.get("/api/auth/config").json() == {"enabled": False}
