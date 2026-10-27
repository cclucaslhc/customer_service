import asyncio
import json
import logging
import re
import uuid
from sqlalchemy.exc import SQLAlchemyError
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from app.identity import AuthenticationError, AuthorizationError
from app.run_leases import RunLease, RunLeaseLost, RunCancellationRequested
from app.admission import CoordinationUnavailable
from app.tracing import agent_scope
from app.model_observability import observe_model
from app.model_fallback import FallbackModel
from app.model_budget import ModelBudgetError

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
from app.database import utc_now
from app.persistence.chat_repository import ChatRepository, LocalChatRepository
from app.knowledge import KnowledgeService
from app.knowledge_retrieval import KnowledgeEvidenceChanged, KnowledgeIndexChanged
from app.providers import ModelGateway, ProviderError
from app.model_failures import classify_model_failure
from app.prompts import runtime_prompt
from app.settings import (
    MAX_CONCURRENT_MODEL_RUNS,
    MODEL_QUEUE_TIMEOUT_SECONDS,
    MODEL_RUN_TIMEOUT_SECONDS,
    SSE_HEARTBEAT_SECONDS,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def timeout_after(seconds: float):
    """Python 3.10 compatible timeout context that preserves external cancellation."""
    task = asyncio.current_task()
    expired = False

    def cancel_for_timeout() -> None:
        nonlocal expired
        expired = True
        if task is not None:
            task.cancel()

    handle = asyncio.get_running_loop().call_later(seconds, cancel_for_timeout)
    try:
        yield
    except asyncio.CancelledError:
        if expired:
            raise asyncio.TimeoutError from None
        raise
    finally:
        handle.cancel()


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


@dataclass
class ChatRuntime:
    """Shared process capacity/cancellation only, never shared request identity.

    Capacity/notification remain process-local. PostgreSQL Run ownership and
    cancellation intent are durable; durable queue Workers use the same runtime
    for their local execution slots, never as the cross-process ownership source.
    """
    limit: int
    tokens: dict = field(default_factory=dict)
    session_runs: dict = field(default_factory=dict)
    finishers: dict = field(default_factory=dict, repr=False)
    active: int = 0
    waiting: int = 0

    def __post_init__(self):
        self.slots = asyncio.Semaphore(self.limit)
        self.lock = asyncio.Lock()


class ChatOrchestrator:
    def __init__(
        self,
        gateway: ModelGateway,
        knowledge: KnowledgeService,
        *,
        max_concurrent_runs: int = MAX_CONCURRENT_MODEL_RUNS,
        queue_timeout: float = MODEL_QUEUE_TIMEOUT_SECONDS,
        run_timeout: float = MODEL_RUN_TIMEOUT_SECONDS,
        heartbeat_interval: float = SSE_HEARTBEAT_SECONDS,
        repository: ChatRepository | None = None,
        prompt_resolver=None,
        runtime=None,
        identity_resolver=None,
        coordination_check=None,
    ) -> None:
        self.gateway = gateway
        self.knowledge = knowledge
        self.repository = repository or LocalChatRepository()
        self.prompt_resolver = prompt_resolver or runtime_prompt
        self.runtime = runtime or ChatRuntime(max_concurrent_runs)
        self.identity_resolver = identity_resolver
        self.coordination_check = coordination_check
        self.tokens = self.runtime.tokens
        self.session_runs = self.runtime.session_runs
        self.max_concurrent_runs = max_concurrent_runs
        self.queue_timeout = queue_timeout
        self.run_timeout = run_timeout
        self.heartbeat_interval = heartbeat_interval
        self._slots = self.runtime.slots
        self._state_lock = self.runtime.lock

    def capacity(self) -> dict:
        return {
            "limit": self.runtime.limit,
            "active": self.runtime.active,
            "waiting": self.runtime.waiting,
            "available": max(0, self.runtime.limit - self.runtime.active),
        }

    async def shutdown(self) -> None:
        async with self._state_lock:
            active = list(self.tokens.items())
            for _, token in active:
                token.cancel()
        for run_id, _ in active:
            finisher = self.runtime.finishers.get(run_id)
            if finisher:
                await asyncio.to_thread(finisher, run_id, "interrupted", "service_shutdown")

    def _finish_run(self, run_id: str, status: str, error_code: str | None = None, *, repository=None) -> None:
        """Finalize a run idempotently without setting a busy session to idle."""
        try:
            (repository or self.repository).finish_run(run_id, status, error_code, utc_now())
        except (AuthenticationError, AuthorizationError):
            # Do not acquire unscoped/system privileges to finish a revoked Run.
            # Expiry-only internal cleanup can safely finalize the fenced Run.
            logger.info("run finalization deferred: identity no longer authorized")
        except SQLAlchemyError as exc:
            logger.warning('run finalization deferred exception_type=%s', type(exc).__name__)

    async def _watch_run_lease(self, repository, token, state):
        try:
            while True:
                await asyncio.sleep(2)
                if self.coordination_check:
                    await asyncio.to_thread(self.coordination_check)
                if not await asyncio.to_thread(repository.renew_lease, raise_on_cancel=True):
                    state['reason'] = 'lease_lost'
                    token.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except RunCancellationRequested:
            state['reason'] = 'cancelled'
            token.cancel()
        except (AuthenticationError, AuthorizationError):
            state['reason'] = 'identity_revoked'
            token.cancel()
        except CoordinationUnavailable:
            state['reason'] = 'coordination_unavailable'
            token.cancel()
        except Exception:
            # Database/transport failure must never extend execution authority.
            state['reason'] = 'lease_check_failed'
            token.cancel()

    async def _events_with_heartbeat(self, events) -> AsyncIterator[object | None]:
        """Keep SSE connections alive without cancelling a slow provider read."""
        iterator = events.__aiter__()
        next_event: asyncio.Task | None = asyncio.create_task(anext(iterator))
        try:
            while True:
                done, _ = await asyncio.wait({next_event}, timeout=self.heartbeat_interval)
                if not done:
                    yield None
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    return
                next_event = asyncio.create_task(anext(iterator))
                yield event
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)

    def create_session(self) -> dict:
        session_id = str(uuid.uuid4())
        now = utc_now()
        return {"id": session_id, "title": "新会话", "status": "idle", "management_status": "open", "assignee": None, "tags": [], "created_at": now, "updated_at": now}

    def list_sessions(self, limit: int = 50, scope: str = "active") -> list[dict]:
        return self.repository.list_sessions(limit, scope)

    def session(self, session_id: str, include_deleted: bool = False) -> dict | None:
        return self.repository.session(session_id, include_deleted)

    def rename_session(self, session_id: str, title: str) -> dict | None:
        now = utc_now()
        clean_title = re.sub(r"\s+", " ", title).strip()
        updated = self.repository.update_session(session_id, now=now, title=clean_title)
        return self.session(session_id) if updated else None

    def _cancel_session(self, session_id: str) -> None:
        self.repository.request_cancel(session_id)
        run_id = self.session_runs.get(session_id)
        if run_id:
            self.cancel(session_id, run_id)

    def archive_session(self, session_id: str) -> dict | None:
        self._cancel_session(session_id)
        now = utc_now()
        updated = self.repository.update_session(session_id, now=now, archived_at=now, status="idle")
        return self.session(session_id) if updated else None

    def restore_session(self, session_id: str) -> dict | None:
        now = utc_now()
        updated = self.repository.update_session(session_id, now=now, include_deleted=True, archived_at=None, deleted_at=None, status="idle")
        return self.session(session_id) if updated else None

    def delete_session(self, session_id: str) -> bool:
        self._cancel_session(session_id)
        now = utc_now()
        return self.repository.update_session(session_id, now=now, deleted_at=now, status="idle")

    def admin_sessions(self, scope: str = "all", limit: int = 100) -> list[dict]:
        return self.repository.list_sessions(limit, scope, admin=True)

    def history(self, session_id: str) -> list[dict]:
        return self.repository.history(session_id)

    def public_history(self, session_id: str) -> list[dict]:
        return [serialize_message(item) for item in self.history(session_id)]

    def set_message_feedback(self, session_id: str, message_id: str, rating: str | None) -> dict | None:
        if not self.repository.feedback(session_id, message_id, rating):
            return None
        return {"message_id": message_id, "rating": rating}

    def _model_history(self, session_id: str, through_sequence=None):
        messages = self.history(session_id)
        if through_sequence is not None:
            messages = [m for m in messages if m.get('sequence',0) <= through_sequence]
        messages = messages[-20:]
        result = []
        for item in messages:
            if item["role"] == "user":
                result.append(ModelRequest(parts=[UserPromptPart(content=item["content"])]))
            elif item["role"] == "assistant":
                result.append(ModelResponse(parts=[TextPart(content=item["content"])]))
        return result

    def cancel(self, session_id: str, run_id: str) -> bool:
        if not self.repository.session(session_id, include_deleted=True):
            return False
        durable = self.repository.request_cancel(session_id, run_id)
        if self.session_runs.get(session_id) != run_id:
            return durable
        token = self.tokens.get(run_id)
        if token:
            token.cancel()
            return True
        return durable

    async def stream(self, session_id: str, content: str, *, prepared=None) -> AsyncIterator[str]:
        run_id = prepared['run_id'] if prepared else str(uuid.uuid4())
        try:
            snapshot = await asyncio.to_thread(self.gateway.snapshot)
            if prepared and (snapshot.provider != prepared['provider'] or snapshot.model != prepared['model']):
                raise ProviderError('configuration_changed','排队期间模型配置已变更，请重新发送')
        except ProviderError as exc:
            if prepared:
                await asyncio.to_thread(self._finish_run,run_id,'error',exc.code,
                    repository=self.repository.with_lease(prepared['lease']))
            yield sse("error", {"code": exc.code, "message": str(exc)})
            return
        except (AuthenticationError, AuthorizationError):
            yield sse("error", {"code": "authentication_required", "message": "登录身份已失效，请重新登录"})
            return

        admitted = False
        persisted = False
        token = CancellationToken()
        try:
            principal = await asyncio.to_thread(self.identity_resolver) if self.identity_resolver else None
        except (AuthenticationError, AuthorizationError):
            yield sse("error", {"code": "authentication_required", "message": "登录身份已失效，请重新登录"})
            return
        deps = AgentDeps(self.knowledge, principal=principal, identity_resolver=self.identity_resolver)
        full_text = ""
        active_tools: dict[str, str] = {}
        tool_records: dict[str, int] = {}
        run_repository = self.repository
        run_route = None
        lease_watch = None
        lease_state = {}
        def finish(run_id, status, error_code=None):
            return self._finish_run(run_id, status, error_code, repository=run_repository)
        try:
            self.runtime.waiting += 1
            try:
                await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
                admitted = True
            except asyncio.TimeoutError:
                yield sse(
                    "error",
                    {
                        "code": "service_busy",
                        "message": "当前咨询量较大，请稍后重试",
                        "retry_after": max(1, round(self.queue_timeout)),
                    },
                )
                return
            finally:
                self.runtime.waiting -= 1

            self.runtime.active += 1

            deps.index_id = await asyncio.to_thread(getattr(self.knowledge, 'active_index_id', lambda: None))

            now = utc_now()
            user_id = str(uuid.uuid4())
            prompt_version_id, run_instructions = (prepared['prompt_version_id'],prepared['instructions']) if prepared else await asyncio.to_thread(self.prompt_resolver)
            use_lease = isinstance(self.repository, ChatRepository) and self.repository.engine.dialect.name == 'postgresql'
            owner = str(uuid.uuid4()) if use_lease else None
            persisted = True if prepared else await asyncio.to_thread(
                self.repository.start_run, session_id=session_id, run_id=run_id, user_id=user_id,
                title=make_session_title(content), content=content, provider=snapshot.provider,
                model=snapshot.model, prompt_version_id=prompt_version_id, now=now,
                **({'lease_owner': owner} if use_lease else {}),
            )
            if not persisted:
                yield sse("error", {"code": "session_inactive", "message": "已归档或删除的会话不能继续发送消息，请先恢复"})
                return
            if use_lease:
                run_repository = self.repository.with_lease(prepared['lease'] if prepared else RunLease(run_id, owner, 1))
                def require_lease():
                    if self.coordination_check:
                        self.coordination_check()
                    if not run_repository.renew_lease(raise_on_cancel=True):
                        raise RunLeaseLost()
                deps.execution_check = require_lease
                lease_watch = asyncio.create_task(self._watch_run_lease(run_repository, token, lease_state))

            if use_lease and isinstance(self.knowledge,KnowledgeService) and self.knowledge.supports_rag_routing():
                from app.rag_routing import binding_for_runtime
                run_route=await asyncio.to_thread(run_repository.pin_rag_route,run_id=run_id,session_id=session_id,
                    runtime_context=binding_for_runtime(snapshot.provider,snapshot.model,run_instructions))
                deps.knowledge=self.knowledge.for_route(run_route)
                deps.index_id=run_route['index_id']

            # Only a successful authorized start may replace/cancel a previous
            # process-local Run. A foreign ID must never touch shared tokens.
            async with self._state_lock:
                old_run = self.session_runs.get(session_id)
                if old_run and old_run in self.tokens:
                    self.tokens[old_run].cancel()
                self.tokens[run_id] = token
                self.session_runs[session_id] = run_id
                self.runtime.finishers[run_id] = finish

            yield sse("run_started", {"run_id": run_id})
            yield sse("provider", {"provider": snapshot.provider, "model": snapshot.model})
            meter = run_repository.model_meter(run_id,provider=snapshot.provider,model=snapshot.model) if use_lease else None
            model = observe_model(self.gateway.build_model(snapshot), provider=snapshot.provider,meter=meter)
            if use_lease and not (run_route and run_route['rollout_id']):
                model = FallbackModel(model, gateway=self.gateway, run_repository=run_repository,
                    run_id=run_id, primary_provider=snapshot.provider, primary_model=snapshot.model)
            visible_provider, visible_model = snapshot.provider, snapshot.model
            history = await asyncio.to_thread(self._model_history, session_id, prepared['user_sequence'] if prepared else None)
            visible_text = ""
            async with timeout_after(self.run_timeout), agent_scope(run_id):
                async with agent.run_stream_events(
                    content,
                    model=model,
                    deps=deps,
                    message_history=history[:-1],
                    cancellation_token=token,
                    run_id=run_id,
                    conversation_id=session_id,
                    instructions=run_instructions,
                ) as events:
                    async for event in self._events_with_heartbeat(events):
                        await asyncio.to_thread(deps.require_identity)
                        if isinstance(model, FallbackModel) and (
                            model.last_provider, model.last_model) != (visible_provider, visible_model):
                            visible_provider, visible_model = model.last_provider, model.last_model
                            yield sse('provider', {'provider': visible_provider, 'model': visible_model})
                        if event is None:
                            yield sse("ping", {"timestamp": utc_now()})
                            continue
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
                                if not deps.knowledge_searched:
                                    yield sse("token", {"text": event.part.content})
                        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                            delta = event.delta.content_delta
                            if delta:
                                visible_text += delta
                                if not deps.knowledge_searched:
                                    yield sse("token", {"text": delta})
                        elif isinstance(event, FunctionToolCallEvent):
                            if visible_text:
                                visible_text = ""
                                yield sse("answer_reset", {})
                            active_tools[event.part.tool_call_id] = event.part.tool_name
                            yield sse("tool_start", {"tool": event.part.tool_name})
                            tool_records[event.part.tool_call_id] = await asyncio.to_thread(
                                run_repository.start_tool, run_id, event.part.tool_name, utc_now(),
                            )
                        elif isinstance(event, FunctionToolResultEvent):
                            tool_name = event.part.tool_name or active_tools.get(event.part.tool_call_id, "unknown")
                            record_id = tool_records.pop(event.part.tool_call_id, None)
                            if record_id is not None:
                                await asyncio.to_thread(run_repository.finish_tool, record_id, utc_now())
                            active_tools.pop(event.part.tool_call_id, None)
                            yield sse("tool_end", {"tool": tool_name})
                        elif isinstance(event, AgentRunResultEvent):
                            full_text = str(event.result.output).strip()
                            # output validator 可能规范化最终内容；确保页面与持久化正文一致。
                            if visible_text != full_text:
                                yield sse("answer_reset", {})
                                visible_text = full_text
                                if full_text and not deps.knowledge_searched:
                                    yield sse("token", {"text": full_text})
            if token.cancelled:
                await asyncio.to_thread(finish, run_id, "interrupted", lease_state.get('reason','cancelled'))
                yield sse("interrupted", {"run_id": run_id})
                return
            if not full_text:
                deps.validation_error = deps.validation_error or "模型未产生最终回答"
                raise RuntimeError(deps.validation_error)
            if deps.citation_sources:
                await asyncio.to_thread(deps.require_identity)
                if hasattr(deps.knowledge, 'require_index'):
                    await asyncio.to_thread(deps.knowledge.require_index, deps.index_id)
                if not await asyncio.to_thread(deps.knowledge.sources_current, deps.citation_sources):
                    raise KnowledgeEvidenceChanged()
            message_id = str(uuid.uuid4())
            if self.coordination_check:
                await asyncio.to_thread(self.coordination_check)
            completed = await asyncio.to_thread(
                run_repository.complete_run, run_id=run_id, session_id=session_id, message_id=message_id,
                content=full_text, provider=visible_provider, model=visible_model, citations=deps.citations, now=utc_now(),
                evidence_sources=deps.citation_sources,
                index_pinned=isinstance(self.knowledge, KnowledgeService), index_id=deps.index_id,
                contact_summary=content[:300] if deps.show_contact else None,
            )
            if not completed:
                yield sse("interrupted", {"run_id": run_id})
                return
            # 检索后的草稿只有在最终校验、来源复核与持久化成功后才可见。
            # 否则输出校验器虽能 reset，错误事实仍可能被 SSE 提前送到用户。
            if deps.knowledge_searched:
                yield sse("token", {"text": full_text})
            for citation in deps.citations:
                yield sse("citation", citation)
            if deps.show_contact:
                yield sse("contact_form", {"summary": content[:300]})
            yield sse("done", {"message_id": message_id, "run_id": run_id})
        except (AuthenticationError, AuthorizationError):
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, "interrupted", "identity_revoked")
            yield sse("answer_reset", {})
            yield sse("error", {"code": "authentication_required", "message": "登录身份已失效，请重新登录"})
        except KnowledgeEvidenceChanged:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'interrupted', 'knowledge_changed')
            yield sse('answer_reset', {})
            yield sse('error', {'code': 'knowledge_changed', 'message': '引用的知识资料已变化，请重新提问'})
        except KnowledgeIndexChanged:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'interrupted', 'knowledge_index_changed')
            yield sse('answer_reset', {})
            yield sse('error', {'code': 'knowledge_changed', 'message': '知识索引已切换，请重新提问'})
        except ModelBudgetError as exc:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'error', exc.code)
            yield sse('answer_reset', {})
            yield sse('error', {'code': exc.code, 'message': exc.message})
        except RunCancelled:
            if persisted:
                await asyncio.to_thread(finish, run_id, "interrupted", lease_state.get('reason', 'cancelled'))
            yield sse('answer_reset', {})
            if lease_state.get('reason') == 'identity_revoked':
                yield sse('error', {'code': 'authentication_required', 'message': '登录身份已失效，请重新登录'})
            else:
                yield sse("interrupted", {"run_id": run_id})
        except CoordinationUnavailable:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'interrupted', 'coordination_unavailable')
            yield sse('answer_reset', {})
            yield sse('interrupted', {'run_id': run_id})
        except RunCancellationRequested:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'interrupted', 'cancelled')
            yield sse('answer_reset', {})
            yield sse('interrupted', {'run_id': run_id})
        except RunLeaseLost:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, 'interrupted', 'lease_lost')
            yield sse('answer_reset', {})
            yield sse('interrupted', {'run_id': run_id})
        except asyncio.TimeoutError:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, "error", "model_timeout")
            yield sse("answer_reset", {})
            yield sse("error", {"code": "model_timeout", "message": "模型响应超时，请稍后重试"})
        except asyncio.CancelledError:
            token.cancel()
            if persisted:
                await asyncio.to_thread(finish, run_id, "interrupted", "client_disconnected")
            raise
        except Exception as exc:
            failed_tool = next(reversed(active_tools.values()), None) if active_tools else None
            failure = classify_model_failure(exc)
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
                "agent run failed code=%s failure_kind=%s delivery=%s run_id=%s session_id=%s provider=%s model=%s tool=%s exception_type=%s",
                error_code,
                failure.kind if not failed_tool else 'tool',
                failure.delivery if not failed_tool else 'unknown',
                run_id,
                session_id,
                snapshot.provider,
                snapshot.model,
                failed_tool or "none",
                type(exc).__name__,
            )
            if persisted:
                await asyncio.to_thread(finish, run_id, "error", error_code)
            yield sse("answer_reset", {})
            yield sse("error", {"code": error_code, "message": message})
        finally:
            if lease_watch is not None:
                lease_watch.cancel()
                await asyncio.gather(lease_watch, return_exceptions=True)
            async with self._state_lock:
                self.tokens.pop(run_id, None)
                self.runtime.finishers.pop(run_id, None)
                if self.session_runs.get(session_id) == run_id:
                    self.session_runs.pop(session_id, None)
            if admitted:
                self.runtime.active -= 1
                self._slots.release()
