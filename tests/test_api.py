import io

import pytest
from fastapi.testclient import TestClient

from app.database import connect, init_database
from app.knowledge import KnowledgeService
from app.main import app


def reset_data() -> None:
    init_database()
    with connect() as db:
        for table in ("contacts", "agent_runs", "messages", "chat_sessions", "knowledge_chunks", "documents"):
            db.execute(f"DELETE FROM {table}")
        db.execute("UPDATE provider_configs SET encrypted_key=NULL, nonce=NULL, last_test_status=NULL, last_test_message=NULL")
        db.execute("UPDATE app_settings SET value='deepseek' WHERE key='active_provider'")


@pytest.fixture(autouse=True)
def clean_database():
    reset_data()
    yield
    reset_data()


def test_health_and_provider_config() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        config = client.get("/api/admin/providers").json()
        assert config["active_provider"] == "deepseek"
        assert {item["provider"] for item in config["providers"]} == {"deepseek", "openai"}
        assert not any(item["configured"] for item in config["providers"])


def test_chat_requires_admin_key() -> None:
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions").json()["id"]
        response = client.post(f"/api/chat/sessions/{session_id}/messages", json={"content": "你好"})
        assert response.status_code == 200
        assert "provider_not_configured" in response.text
        assert "管理页" in response.text


def test_upload_search_delete_document() -> None:
    with TestClient(app) as client:
        data = "云小智 Pro 提供两年质保。签收后七天内可以申请售后。".encode()
        response = client.post("/api/admin/documents", files={"file": ("policy.txt", io.BytesIO(data), "text/plain")})
        assert response.status_code == 200, response.text
        document = response.json()
        assert document["chunk_count"] >= 1
        rows = client.get("/api/admin/documents").json()
        assert rows[0]["display_name"] == "policy.txt"
        hits = KnowledgeService().search("云小智 Pro 质保")
        assert hits and "两年质保" in hits[0]["content"]
        client.delete(f"/api/admin/documents/{document['id']}")
        assert client.get("/api/admin/documents").json() == []


def test_contact_validation_and_storage() -> None:
    with TestClient(app) as client:
        invalid = client.post("/api/contacts", json={"name": "张先生", "summary": "需要帮助"})
        assert invalid.status_code == 422
        saved = client.post("/api/contacts", json={"name": "张先生", "phone": "13800138000", "summary": "咨询订单"})
        assert saved.status_code == 200
        contacts = client.get("/api/admin/contacts").json()
        assert contacts[0]["phone"] == "13800138000"


def test_provider_key_is_write_only_and_encrypted() -> None:
    fake_key = "sk-demo-this-is-not-a-real-secret"
    with TestClient(app) as client:
        response = client.put(
            "/api/admin/providers/deepseek",
            json={"model": "deepseek-chat", "api_key": fake_key},
        )
        assert response.status_code == 200
        body = response.text
        assert fake_key not in body
        config = response.json()
        deepseek = next(item for item in config["providers"] if item["provider"] == "deepseek")
        assert deepseek["configured"] is True
    with connect() as db:
        row = db.execute("SELECT encrypted_key FROM provider_configs WHERE provider='deepseek'").fetchone()
        assert row["encrypted_key"] and fake_key.encode() not in row["encrypted_key"]
