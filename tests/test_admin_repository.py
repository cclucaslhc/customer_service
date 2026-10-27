"""Administrative repository checks using synthetic isolated records."""

import pytest
import sqlalchemy as sa

from app import admin
from app.database import utc_now
from app.persistence.admin_repository import AdminRepository
from test_chat_repository import repository, start


@pytest.fixture
def managed(repository):
    return AdminRepository(repository.engine)


def test_filters_details_and_safe_field_updates(managed, monkeypatch):
    assert start(managed, "first", "first-run")
    managed.finish_run("first-run", "error", "synthetic_failure", utc_now())
    assert start(managed, "second", "second-run")
    managed.finish_run("second-run", "complete", None, utc_now())
    managed.update_session("first", now=utc_now(), title="Synthetic 100%", tags_json=["priority%", "test"], assignee="synthetic-assignee")
    for filters in ({"q": "100%"}, {"tag": "priority%"}, {"assignee": "synthetic-assignee"}, {"run_status": "error"}):
        result = managed.managed_sessions(filters, 1, 10)
        assert result["total"] == 1 and result["items"][0]["id"] == "first"
    assert managed.managed_sessions({"tag": "priority"}, 1, 10)["total"] == 0
    assert managed.managed_sessions({}, 1, 1)["total"] == 2
    assert len(managed.managed_sessions({}, 2, 1)["items"]) == 1
    managed.add_note("first", " synthetic note ", utc_now())
    assert managed.add_note("missing", "note", utc_now()) is None
    managed.audit(action="session.test", entity_type="session", entity_id="first", summary="synthetic", metadata={"safe": True}, now=utc_now())
    detail = managed.detail("first")
    assert detail["runs"][0]["error_code"] == "synthetic_failure"
    assert detail["notes"][0]["content"] == "synthetic note"
    assert {row["action"] for row in detail["audit"]} == {"session.test", "session.note"}
    assert managed.detail("missing") is None
    monkeypatch.setattr(admin, "repository", managed)
    changed = admin.update_session("first", {"tags": ["new"], "management_status": "resolved", "status": "ignored"})
    assert changed["session"]["tags"] == ["new"]
    assert changed["session"]["status"] == "idle"
    assert changed["runs"][0]["duration_ms"] >= 0


def test_contact_association_paging_and_deleted_session(managed):
    assert start(managed)
    cid = managed.create_contact(session_id="session", name="Synthetic", phone=None, email="test@synthetic.invalid", summary="100% synthetic", now=utc_now())
    assert cid
    assert managed.managed_sessions({"has_contact": True}, 1, 10)["total"] == 1
    assert managed.managed_sessions({"has_contact": False}, 1, 10)["total"] == 0
    assert managed.list_contacts("100%", None, 1, 10)["total"] == 1
    assert managed.list_contacts("not-found", None, 1, 10)["total"] == 0
    assert managed.update_contact(cid, {"status": "following", "note": "synthetic"})["status"] == "following"
    assert managed.list_contacts(None, "new", 1, 10)["total"] == 0
    assert managed.list_contacts(None, "following", 1, 10)["total"] == 1
    assert managed.update_contact("missing", {"note": "synthetic"}) is None
    with pytest.raises(ValueError):
        managed.update_contact(cid, {"name": "forbidden-field"})
    with pytest.raises(ValueError):
        managed.update_contact(cid, {"status": None})
    managed.update_session("session", now=utc_now(), deleted_at=utc_now())
    assert managed.create_contact(session_id="session", name="Synthetic", phone=None, email=None, summary="test", now=utc_now()) is None
    assert managed.create_contact(session_id="missing", name="Synthetic", phone=None, email=None, summary="test", now=utc_now()) is None
    assert managed.list_contacts(None, None, 1, 10)["total"] == 1


def test_overview_never_exposes_provider_key_and_audit_filter(managed):
    managed.audit(action="synthetic%action", entity_type="test", entity_id=None, summary="synthetic", metadata={}, now=utc_now())
    assert managed.list_audit(1, 1, "%action")["total"] == 1
    assert managed.list_audit(1, 1, "missing")["total"] == 0
    overview = managed.overview()
    assert overview["sessions"] == overview["runs"] == 0
    assert overview["completion_rate"] == overview["error_rate"] == 0
    assert overview["active_provider"] == "deepseek"
    assert all("encrypted_key" not in row and "nonce" not in row for row in overview["providers"])


def test_contact_and_note_changes_roll_back_when_audit_fails(managed, monkeypatch):
    start(managed)
    contact = managed.create_contact(session_id="session", name="Synthetic", phone=None, email=None, summary="Synthetic", now=utc_now())
    def unavailable(*args, **kwargs):
        raise RuntimeError("Synthetic audit outage")
    monkeypatch.setattr(managed, "_write_audit", unavailable)
    with pytest.raises(RuntimeError, match="audit outage"):
        managed.create_contact(session_id=None, name="Synthetic", phone=None, email=None, summary="Synthetic", now=utc_now())
    with pytest.raises(RuntimeError, match="audit outage"):
        managed.update_contact(contact, {"status": "closed"})
    with pytest.raises(RuntimeError, match="audit outage"):
        managed.add_note("session", "Synthetic", utc_now())
    assert managed.list_contacts(None, None, 1, 10)["total"] == 1
    assert managed.list_contacts(None, None, 1, 10)["items"][0]["status"] == "new"
    assert managed.detail("session")["notes"] == []
