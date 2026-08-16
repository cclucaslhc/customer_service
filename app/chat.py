import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.agent import AgentDeps, agent
from app.database import connect, utc_now
from app.knowledge import KnowledgeService
from app.providers import ModelGateway, ProviderError


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatOrchestrator:
    def __init__(self, gateway: ModelGateway, knowledge: KnowledgeService) -> None:
        self.gateway = gateway
        self.knowledge = knowledge
        self.tokens: dict[str, CancellationToken] = {}
        self.session_runs: dict[str, str] = {}
        self.lock = asyncio.Lock()

    def create_session(self) -> dict:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with connect() as db:
            db.execute("INSERT INTO chat_sessions(id,status,created_at,updated_at) VALUES(?,?,?,?)", (session_id, "idle", now, now))
        return {"id": session_id, "status": "idle", "created_at": now}

    def history(self, session_id: str) -> list[dict]:
        with connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE session_id=? AND status='complete' ORDER BY created_at", (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def _model_history(self, session_id: str):
        messages = self.history(session_id)[-20:]
        result = []
        for item in messages:
            if item["role"] == "user":
                result.append(ModelRequest(parts=[UserPromptPart(content=item["content"])]))
            elif item["role"] == "assistant":
                result.append(ModelResponse(parts=[TextPart(content=item["content"])]))
        return result

    def cancel(self, session_id: str, run_id: str) -> bool:
        if self.session_runs.get(session_id) != run_id:
            return False
        token = self.tokens.get(run_id)
        if token:
            token.cancel()
            return True
        return False

    async def stream(self, session_id: str, content: str) -> AsyncIterator[str]:
        run_id = str(uuid.uuid4())
        old_run = self.session_runs.get(session_id)
        if old_run and old_run in self.tokens:
            self.tokens[old_run].cancel()
        token = CancellationToken()
        self.tokens[run_id] = token
        self.session_runs[session_id] = run_id
        try:
            snapshot = self.gateway.snapshot()
        except ProviderError as exc:
            yield sse("error", {"code": exc.code, "message": str(exc)})
            return
        now = utc_now()
        user_id = str(uuid.uuid4())
        with connect() as db:
            exists = db.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
            if not exists:
                yield sse("error", {"code": "session_not_found", "message": "会话不存在"})
                return
            db.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (user_id, session_id, "user", content, now))
            db.execute("INSERT INTO agent_runs(id,session_id,status,provider,model,started_at) VALUES(?,?,?,?,?,?)", (run_id, session_id, "running", snapshot.provider, snapshot.model, now))
            db.execute("UPDATE chat_sessions SET status='running',updated_at=? WHERE id=?", (now, session_id))
        yield sse("run_started", {"run_id": run_id})
        yield sse("provider", {"provider": snapshot.provider, "model": snapshot.model})
        deps = AgentDeps(self.knowledge)
        full_text = ""
        try:
            model = self.gateway.build_model(snapshot)
            async with agent.run_stream(
                content,
                model=model,
                deps=deps,
                message_history=self._model_history(session_id)[:-1],
                cancellation_token=token,
                run_id=run_id,
                conversation_id=session_id,
            ) as result:
                async for delta in result.stream_text(delta=True):
                    if token.cancelled:
                        break
                    full_text += delta
                    yield sse("token", {"text": delta})
            if token.cancelled:
                with connect() as db:
                    db.execute("UPDATE agent_runs SET status='interrupted',finished_at=? WHERE id=?", (utc_now(), run_id))
                    db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
                yield sse("interrupted", {"run_id": run_id})
                return
            message_id = str(uuid.uuid4())
            citations_json = json.dumps(deps.citations, ensure_ascii=False)
            with connect() as db:
                db.execute(
                    "INSERT INTO messages(id,session_id,role,content,provider,model,citations_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (message_id, session_id, "assistant", full_text, snapshot.provider, snapshot.model, citations_json, utc_now()),
                )
                db.execute("UPDATE agent_runs SET status='complete',finished_at=? WHERE id=?", (utc_now(), run_id))
                db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
            for citation in deps.citations:
                yield sse("citation", citation)
            if deps.show_contact:
                yield sse("contact_form", {"summary": content[:300]})
            yield sse("done", {"message_id": message_id, "run_id": run_id})
        except Exception as exc:
            with connect() as db:
                db.execute("UPDATE agent_runs SET status='error',error_code='model_error',finished_at=? WHERE id=?", (utc_now(), run_id))
                db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
            yield sse("error", {"code": "model_error", "message": "模型调用失败，请检查供应商配置或稍后重试"})
        finally:
            self.tokens.pop(run_id, None)
            if self.session_runs.get(session_id) == run_id:
                self.session_runs.pop(session_id, None)
