import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator

from pydantic_ai import AgentRunResultEvent, CancellationToken
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)

from app.agent import AgentDeps, agent
from app.database import connect, utc_now
from app.knowledge import KnowledgeService
from app.providers import ModelGateway, ProviderError
from app.prompts import runtime_prompt


logger = logging.getLogger(__name__)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def make_session_title(content: str, limit: int = 36) -> str:
    title = re.sub(r"\s+", " ", content).strip()
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


def serialize_message(row: dict) -> dict:
    citations = row.get("citations_json", "[]")
    try:
        citations = json.loads(citations) if isinstance(citations, str) else citations
    except json.JSONDecodeError:
        citations = []
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "status": row["status"],
        "provider": row.get("provider"),
        "model": row.get("model"),
        "feedback": row.get("feedback"),
        "citations": citations,
        "created_at": row["created_at"],
    }


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
        return {"id": session_id, "title": "新会话", "status": "idle", "management_status": "open", "assignee": None, "tags": [], "created_at": now, "updated_at": now}

    def list_sessions(self, limit: int = 50, scope: str = "active") -> list[dict]:
        lifecycle = "s.deleted_at IS NULL AND s.archived_at IS NULL" if scope == "active" else "s.deleted_at IS NULL AND s.archived_at IS NOT NULL"
        with connect() as db:
            rows = db.execute(
                f"""
                SELECT s.id,coalesce(nullif(trim(s.title),''),'新会话') title,s.status,s.archived_at,s.deleted_at,s.created_at,s.updated_at,
                       count(m.id) message_count,
                       (SELECT content FROM messages latest
                        WHERE latest.session_id=s.id AND latest.status='complete'
                        ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1) last_message
                FROM chat_sessions s
                JOIN messages m ON m.session_id=s.id AND m.status='complete'
                WHERE {lifecycle}
                GROUP BY s.id
                ORDER BY s.updated_at DESC,s.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def session(self, session_id: str, include_deleted: bool = False) -> dict | None:
        with connect() as db:
            row = db.execute(
                """SELECT id,coalesce(nullif(trim(title),''),'新会话') title,status,archived_at,deleted_at,created_at,updated_at
                   FROM chat_sessions WHERE id=? AND (? OR deleted_at IS NULL)""",
                (session_id, include_deleted),
            ).fetchone()
        return dict(row) if row else None

    def rename_session(self, session_id: str, title: str) -> dict | None:
        now = utc_now()
        clean_title = re.sub(r"\s+", " ", title).strip()
        with connect() as db:
            result = db.execute(
                "UPDATE chat_sessions SET title=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                (clean_title, now, session_id),
            )
        return self.session(session_id) if result.rowcount else None

    def _cancel_session(self, session_id: str) -> None:
        run_id = self.session_runs.get(session_id)
        if run_id:
            self.cancel(session_id, run_id)

    def archive_session(self, session_id: str) -> dict | None:
        self._cancel_session(session_id)
        now = utc_now()
        with connect() as db:
            result = db.execute(
                "UPDATE chat_sessions SET archived_at=?,status='idle',updated_at=? WHERE id=? AND deleted_at IS NULL",
                (now, now, session_id),
            )
        return self.session(session_id) if result.rowcount else None

    def restore_session(self, session_id: str) -> dict | None:
        now = utc_now()
        with connect() as db:
            result = db.execute(
                "UPDATE chat_sessions SET archived_at=NULL,deleted_at=NULL,status='idle',updated_at=? WHERE id=?",
                (now, session_id),
            )
        return self.session(session_id) if result.rowcount else None

    def delete_session(self, session_id: str) -> bool:
        self._cancel_session(session_id)
        now = utc_now()
        with connect() as db:
            result = db.execute(
                "UPDATE chat_sessions SET deleted_at=?,status='idle',updated_at=? WHERE id=? AND deleted_at IS NULL",
                (now, now, session_id),
            )
        return bool(result.rowcount)

    def admin_sessions(self, scope: str = "all", limit: int = 100) -> list[dict]:
        filters = {
            "active": "s.deleted_at IS NULL AND s.archived_at IS NULL",
            "archived": "s.deleted_at IS NULL AND s.archived_at IS NOT NULL",
            "deleted": "s.deleted_at IS NOT NULL",
            "all": "1=1",
        }
        with connect() as db:
            rows = db.execute(
                f"""
                SELECT s.id,coalesce(nullif(trim(s.title),''),'新会话') title,s.status,s.archived_at,s.deleted_at,
                       s.created_at,s.updated_at,
                       (SELECT count(*) FROM messages m WHERE m.session_id=s.id AND m.status='complete') message_count,
                       (SELECT count(*) FROM agent_runs r WHERE r.session_id=s.id) run_count,
                       (SELECT count(*) FROM agent_runs r WHERE r.session_id=s.id AND r.status='error') error_count,
                       (SELECT error_code FROM agent_runs r WHERE r.session_id=s.id AND r.error_code IS NOT NULL ORDER BY r.started_at DESC LIMIT 1) last_error_code,
                       (SELECT provider FROM agent_runs r WHERE r.session_id=s.id ORDER BY r.started_at DESC LIMIT 1) provider,
                       (SELECT model FROM agent_runs r WHERE r.session_id=s.id ORDER BY r.started_at DESC LIMIT 1) model,
                       (SELECT count(*) FROM contacts c WHERE c.session_id=s.id) contact_count
                FROM chat_sessions s WHERE {filters[scope]}
                ORDER BY s.updated_at DESC,s.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def history(self, session_id: str) -> list[dict]:
        with connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE session_id=? AND status='complete' ORDER BY created_at", (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def public_history(self, session_id: str) -> list[dict]:
        return [serialize_message(item) for item in self.history(session_id)]

    def set_message_feedback(self, session_id: str, message_id: str, rating: str | None) -> dict | None:
        with connect() as db:
            message = db.execute(
                """SELECT m.id,m.role FROM messages m
                   JOIN chat_sessions s ON s.id=m.session_id
                   WHERE m.id=? AND m.session_id=? AND s.deleted_at IS NULL""",
                (message_id, session_id),
            ).fetchone()
            if not message or message["role"] != "assistant":
                return None
            db.execute("UPDATE messages SET feedback=? WHERE id=?", (rating, message_id))
        return {"message_id": message_id, "rating": rating}

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
        prompt_version_id, run_instructions = runtime_prompt()
        with connect() as db:
            exists = db.execute("SELECT id,title,archived_at,deleted_at FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
            if exists and (exists["deleted_at"] or exists["archived_at"]):
                yield sse("error", {"code": "session_inactive", "message": "已归档或删除的会话不能继续发送消息，请先恢复"})
                return
            if not exists:
                db.execute(
                    "INSERT INTO chat_sessions(id,title,status,created_at,updated_at) VALUES(?,?,?, ?,?)",
                    (session_id, make_session_title(content), "idle", now, now),
                )
                exists = db.execute("SELECT id,title,archived_at,deleted_at FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
            db.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (user_id, session_id, "user", content, now))
            db.execute("INSERT INTO agent_runs(id,session_id,status,provider,model,prompt_version_id,started_at) VALUES(?,?,?,?,?,?,?)", (run_id, session_id, "running", snapshot.provider, snapshot.model, prompt_version_id, now))
            if not exists["title"] or not exists["title"].strip():
                db.execute("UPDATE chat_sessions SET title=?,status='running',updated_at=? WHERE id=?", (make_session_title(content), now, session_id))
            else:
                db.execute("UPDATE chat_sessions SET status='running',updated_at=? WHERE id=?", (now, session_id))
        yield sse("run_started", {"run_id": run_id})
        yield sse("provider", {"provider": snapshot.provider, "model": snapshot.model})
        deps = AgentDeps(self.knowledge)
        full_text = ""
        active_tools: dict[str, str] = {}
        try:
            model = self.gateway.build_model(snapshot)
            visible_text = ""
            async with agent.run_stream_events(
                content,
                model=model,
                deps=deps,
                message_history=self._model_history(session_id)[:-1],
                cancellation_token=token,
                run_id=run_id,
                conversation_id=session_id,
                instructions=run_instructions,
            ) as events:
                async for event in events:
                    if token.cancelled:
                        events.cancel()
                        break
                    if isinstance(event, PartStartEvent):
                        # 每次模型请求的 part 索引从 0 开始。新请求意味着前一段文本只是
                        # 工具调用前说明或校验失败的草稿，应从用户可见答案中移除。
                        if event.index == 0 and visible_text:
                            visible_text = ""
                            yield sse("answer_reset", {})
                        if isinstance(event.part, TextPart) and event.part.content:
                            visible_text += event.part.content
                            yield sse("token", {"text": event.part.content})
                    elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                        delta = event.delta.content_delta
                        if delta:
                            visible_text += delta
                            yield sse("token", {"text": delta})
                    elif isinstance(event, FunctionToolCallEvent):
                        if visible_text:
                            visible_text = ""
                            yield sse("answer_reset", {})
                        active_tools[event.part.tool_call_id] = event.part.tool_name
                        yield sse("tool_start", {"tool": event.part.tool_name})
                        with connect() as db:
                            db.execute("INSERT INTO agent_run_tools(run_id,tool_name,status,started_at) VALUES(?,?,?,?)", (run_id, event.part.tool_name, "running", utc_now()))
                    elif isinstance(event, FunctionToolResultEvent):
                        tool_name = event.part.tool_name or active_tools.get(event.part.tool_call_id, "unknown")
                        with connect() as db:
                            db.execute(
                                "UPDATE agent_run_tools SET status='complete',finished_at=? WHERE id=(SELECT id FROM agent_run_tools WHERE run_id=? AND tool_name=? AND finished_at IS NULL ORDER BY id DESC LIMIT 1)",
                                (utc_now(), run_id, tool_name),
                            )
                        active_tools.pop(event.part.tool_call_id, None)
                        yield sse("tool_end", {"tool": tool_name})
                    elif isinstance(event, AgentRunResultEvent):
                        full_text = str(event.result.output).strip()
                        # output validator 可能规范化最终内容；确保页面与持久化正文一致。
                        if visible_text != full_text:
                            yield sse("answer_reset", {})
                            visible_text = full_text
                            if full_text:
                                yield sse("token", {"text": full_text})
            if token.cancelled:
                with connect() as db:
                    db.execute("UPDATE agent_runs SET status='interrupted',finished_at=? WHERE id=?", (utc_now(), run_id))
                    db.execute("UPDATE agent_run_tools SET status='interrupted',finished_at=? WHERE run_id=? AND finished_at IS NULL", (utc_now(), run_id))
                    db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
                yield sse("interrupted", {"run_id": run_id})
                return
            if not full_text:
                deps.validation_error = deps.validation_error or "模型未产生最终回答"
                raise RuntimeError(deps.validation_error)
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
        except RunCancelled:
            with connect() as db:
                db.execute("UPDATE agent_runs SET status='interrupted',finished_at=? WHERE id=?", (utc_now(), run_id))
                db.execute("UPDATE agent_run_tools SET status='interrupted',finished_at=? WHERE run_id=? AND finished_at IS NULL", (utc_now(), run_id))
                db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
            yield sse("interrupted", {"run_id": run_id})
        except Exception as exc:
            failed_tool = next(reversed(active_tools.values()), None) if active_tools else None
            if deps.validation_error:
                error_code = "answer_validation_failed"
                message = "未能生成完整回答，请重新提问或稍后重试"
            elif failed_tool:
                error_code = "tool_error"
                message = "业务工具执行失败，请稍后重试；若持续发生请联系管理员"
            else:
                error_code = "model_error"
                message = "模型调用失败，请检查供应商配置或稍后重试"
            # 安全日志只保留定位所需的内部标识和异常类型。不要记录用户正文、
            # 工具参数/结果、供应商响应正文或 API Key。
            logger.error(
                "agent run failed code=%s run_id=%s session_id=%s provider=%s model=%s tool=%s exception_type=%s",
                error_code,
                run_id,
                session_id,
                snapshot.provider,
                snapshot.model,
                failed_tool or "none",
                type(exc).__name__,
            )
            with connect() as db:
                db.execute("UPDATE agent_runs SET status='error',error_code=?,finished_at=? WHERE id=?", (error_code, utc_now(), run_id))
                db.execute("UPDATE agent_run_tools SET status='error',finished_at=? WHERE run_id=? AND finished_at IS NULL", (utc_now(), run_id))
                db.execute("UPDATE chat_sessions SET status='idle',updated_at=? WHERE id=?", (utc_now(), session_id))
            if deps.validation_error:
                yield sse("answer_reset", {})
            yield sse("error", {"code": error_code, "message": message})
        finally:
            self.tokens.pop(run_id, None)
            if self.session_runs.get(session_id) == run_id:
                self.session_runs.pop(session_id, None)
