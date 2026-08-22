import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_ai.models.function import FunctionModel

import app.database as database_module
from app.chat import ChatOrchestrator
from app.database import connect, init_database, recover_interrupted_runs, utc_now
from app.providers import ProviderSnapshot


class StubGateway:
    def __init__(self, model: FunctionModel) -> None:
        self.model = model

    def snapshot(self) -> ProviderSnapshot:
        return ProviderSnapshot("deepseek", "function-test", "unused")

    def build_model(self, _: ProviderSnapshot) -> FunctionModel:
        return self.model


class StubKnowledge:
    def search(self, _: str, limit: int = 6) -> list[dict]:
        return []


async def collect(orchestrator: ChatOrchestrator, session_id: str) -> list[dict]:
    frames = []
    async for raw in orchestrator.stream(session_id, "并发稳定性测试"):
        event_line, data_line = raw.strip().split("\n", 1)
        frames.append({
            "event": event_line.removeprefix("event: "),
            "data": json.loads(data_line.removeprefix("data: ")),
        })
    return frames


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database_module, "DATABASE_PATH", tmp_path / "app.db")
    init_database()


def test_sqlite_uses_wal_and_recovers_abandoned_runs() -> None:
    now = utc_now()
    with connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        db.execute(
            "INSERT INTO chat_sessions(id,status,created_at,updated_at) VALUES(?,?,?,?)",
            ("session", "running", now, now),
        )
        db.execute(
            "INSERT INTO agent_runs(id,session_id,status,started_at) VALUES(?,?,?,?)",
            ("run", "session", "running", now),
        )
        db.execute(
            "INSERT INTO agent_run_tools(run_id,tool_name,status,started_at) VALUES(?,?,?,?)",
            ("run", "search_knowledge", "running", now),
        )

    assert recover_interrupted_runs() == 1
    with connect() as db:
        run = db.execute("SELECT status,error_code,finished_at FROM agent_runs WHERE id='run'").fetchone()
        tool = db.execute("SELECT status,finished_at FROM agent_run_tools WHERE run_id='run'").fetchone()
        session = db.execute("SELECT status FROM chat_sessions WHERE id='session'").fetchone()
    assert run["status"] == "interrupted"
    assert run["error_code"] == "service_restarted"
    assert run["finished_at"]
    assert tool["status"] == "interrupted" and tool["finished_at"]
    assert session["status"] == "idle"


def test_concurrent_short_writes_do_not_fail_with_database_locked() -> None:
    def insert_contact(_: int) -> None:
        with connect() as db:
            db.execute(
                "INSERT INTO contacts(id,name,summary,created_at) VALUES(?,?,?,?)",
                (str(uuid.uuid4()), "并发访客", "并发写入验证", utc_now()),
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(insert_contact, range(64)))

    with connect() as db:
        assert db.execute("SELECT count(*) FROM contacts").fetchone()[0] == 64


@pytest.mark.asyncio
async def test_concurrency_bulkhead_rejects_excess_work_and_recovers_capacity() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_model(_, __):
        entered.set()
        await release.wait()
        yield "服务正常。"

    orchestrator = ChatOrchestrator(
        StubGateway(FunctionModel(stream_function=slow_model)),
        StubKnowledge(),
        max_concurrent_runs=1,
        queue_timeout=0.02,
        run_timeout=2,
        heartbeat_interval=0.01,
    )
    first = asyncio.create_task(collect(orchestrator, "first-session"))
    await asyncio.wait_for(entered.wait(), timeout=1)

    rejected = await collect(orchestrator, "second-session")
    assert rejected[-1]["event"] == "error"
    assert rejected[-1]["data"]["code"] == "service_busy"
    assert orchestrator.capacity()["active"] == 1

    release.set()
    completed = await first
    assert completed[-1]["event"] == "done"
    assert orchestrator.capacity() == {"limit": 1, "active": 0, "waiting": 0, "available": 1}


@pytest.mark.asyncio
async def test_slow_provider_emits_heartbeats_then_times_out_cleanly() -> None:
    async def stalled_model(_, __):
        await asyncio.sleep(1)
        yield "不会到达这里"

    orchestrator = ChatOrchestrator(
        StubGateway(FunctionModel(stream_function=stalled_model)),
        StubKnowledge(),
        max_concurrent_runs=1,
        queue_timeout=0.1,
        run_timeout=0.06,
        heartbeat_interval=0.01,
    )
    frames = await collect(orchestrator, "timeout-session")

    assert "ping" in [frame["event"] for frame in frames]
    assert frames[-1]["event"] == "error"
    assert frames[-1]["data"]["code"] == "model_timeout"
    with connect() as db:
        run = db.execute("SELECT status,error_code FROM agent_runs WHERE session_id='timeout-session'").fetchone()
    assert dict(run) == {"status": "error", "error_code": "model_timeout"}
    assert orchestrator.capacity()["active"] == 0


@pytest.mark.asyncio
async def test_client_disconnect_marks_run_interrupted_and_releases_slot() -> None:
    entered = asyncio.Event()

    async def stalled_model(_, __):
        entered.set()
        await asyncio.sleep(10)
        yield "不会到达这里"

    orchestrator = ChatOrchestrator(
        StubGateway(FunctionModel(stream_function=stalled_model)),
        StubKnowledge(),
        max_concurrent_runs=1,
        run_timeout=20,
        heartbeat_interval=0.01,
    )
    request = asyncio.create_task(collect(orchestrator, "disconnect-session"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    with connect() as db:
        run = db.execute("SELECT status,error_code FROM agent_runs WHERE session_id='disconnect-session'").fetchone()
    assert dict(run) == {"status": "interrupted", "error_code": "client_disconnected"}
    assert orchestrator.capacity()["active"] == 0
