"""Repository transaction invariants verified against isolated local storage."""

from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
import sqlalchemy as sa

import app.database as database
from app.persistence.chat_repository import ChatRepository


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / "chat.sqlite"
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    monkeypatch.setattr(database, "ensure_directories", lambda: None)
    database.init_database()
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(path)), hide_parameters=True)
    @sa.event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    repo = ChatRepository(engine)
    yield repo
    engine.dispose()


def start(repository, session_id="session", run_id="run"):
    return repository.start_run(session_id=session_id, run_id=run_id, user_id=str(uuid.uuid4()), title="Title",
                                content="Question", provider="deepseek", model="test", prompt_version_id=None,
                                now=database.utc_now())


def test_completion_atomicity_and_terminal_idempotency(repository):
    assert start(repository)
    now = database.utc_now()
    assert repository.complete_run(run_id="run", session_id="session", message_id="answer", content="Answer",
                                   provider="deepseek", model="test", citations=[], now=now)
    assert not repository.complete_run(run_id="run", session_id="session", message_id="duplicate", content="wrong",
                                       provider="deepseek", model="test", citations=[], now=now)
    assert not repository.finish_run("run", "error", "late_failure", now)
    assert [row["content"] for row in repository.history("session")] == ["Question", "Answer"]
    assert repository.session("session")["status"] == "idle"


def test_answer_insert_failure_rolls_back_run(repository):
    start(repository)
    with pytest.raises(sa.exc.IntegrityError):
        repository.complete_run(run_id="run", session_id="session", message_id="answer", content=None,
                                provider="deepseek", model="test", citations=[], now=database.utc_now())
    assert repository.session("session")["status"] == "running"
    assert len(repository.history("session")) == 1


def test_interrupted_run_cannot_store_answer_or_clear_new_run(repository):
    start(repository, run_id="old")
    start(repository, run_id="new")
    repository.finish_run("old", "interrupted", "cancelled", database.utc_now())
    assert repository.session("session")["status"] == "running"
    assert not repository.complete_run(run_id="old", session_id="session", message_id="late", content="Late",
                                       provider="deepseek", model="test", citations=[], now=database.utc_now())
    assert not repository.complete_run(run_id="new", session_id="other", message_id="wrong-owner", content="Late",
                                       provider="deepseek", model="test", citations=[], now=database.utc_now())


def test_tool_results_use_exact_record_not_latest_same_name(repository):
    start(repository)
    first = repository.start_tool("run", "search_knowledge", database.utc_now())
    second = repository.start_tool("run", "search_knowledge", database.utc_now())
    repository.finish_tool(first, database.utc_now())
    with repository.engine.connect() as connection:
        table = repository.tables["agent_run_tools"]
        records = connection.execute(sa.select(table.c.id, table.c.status).order_by(table.c.id)).all()
    assert records == [(first, "complete"), (second, "running")]
    repository.finish_run("run", "interrupted", "cancelled", database.utc_now())


def test_inactive_session_start_has_no_side_effects(repository):
    start(repository)
    repository.finish_run("run", "complete", None, database.utc_now())
    repository.update_session("session", now=database.utc_now(), archived_at=database.utc_now())
    assert not start(repository, run_id="blocked")
    assert len(repository.history("session")) == 1
    assert repository.list_sessions() == []
    assert len(repository.list_sessions(scope="archived")) == 1


def test_parallel_start_and_finish_share_session_transaction_lock(repository):
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(lambda i: start(repository, run_id=f"run-{i}"), range(8)))
        assert all(executor.map(lambda i: repository.finish_run(f"run-{i}", "interrupted", "synthetic", database.utc_now()), range(8)))
    assert len(repository.history("session")) == 8
    assert repository.session("session")["status"] == "idle"


def test_archived_session_rejects_late_answer_transactionally(repository):
    start(repository)
    repository.update_session("session", now=database.utc_now(), archived_at=database.utc_now())
    assert not repository.complete_run(run_id="run", session_id="session", message_id="late", content="Late",
                                       provider="deepseek", model="test", citations=[], now=database.utc_now())
    assert len(repository.history("session")) == 1
    with repository.engine.connect() as db:
        r = repository.tables["agent_runs"]
        row = db.execute(sa.select(r)).mappings().one()
        assert row["status"] == "interrupted" and row["error_code"] == "session_inactive"
    with pytest.raises(ValueError):
        repository.finish_run("run", "running", None, database.utc_now())
