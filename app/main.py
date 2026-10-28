import asyncio
import uuid
from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.chat import ChatOrchestrator
from app.admin import AdminService
from app.database import health as local_database_health, init_database, recover_interrupted_runs, utc_now
from app.knowledge import KnowledgeDuplicateError, KnowledgeError, KnowledgeService, decode_json
from app.object_store import ObjectStoreError
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_terms import DEFAULT_SYNONYM_GROUPS, all_synonym_groups, custom_synonym_groups, save_custom_synonym_groups
from app.providers import ModelGateway, ProviderError
from app.schemas import ActiveProviderUpdate, AdminSessionUpdate, ChatMessageInput, ContactAdminUpdate, ContactInput, KnowledgePathInput, KnowledgeSearchInput, KnowledgeTermsUpdate, MessageFeedbackInput, ModelBudgetPublish, ModelFallbackConsentPublish, ModelRouteBudgetPublish, ModelPricePublish, PromptDraftInput, PromptRollbackInput, ProviderTest, ProviderUpdate, SessionNoteInput, SessionTitleUpdate
from app.prompts import PromptService
from app.security import SecretStore
from app.identity import AuthenticationError, AuthorizationError
from app.run_leases import run_lease_reaper
from app.settings import FRONTEND_DIST, MAX_UPLOAD_BYTES
from app.http_observability import ObservedFastAPI
from app.tracing import capture_carrier, install_database_tracing


def create_app(*, repositories=None, secret_store=None, knowledge_paths=None, auth_policy=None,durable_chat=False,event_retention_seconds=None,durable_knowledge=False,storage_policy=None,http_telemetry=None,metrics_policy=None,tracing=None,local_model_endpoint=None,rag_embedding_gateway=None,rag_reranker=None,allow_authenticated_proxy_clients=False):
    """Explicit storage injection; default HTTP boundary is localhost-only.

    PostgreSQL assembly is available for integration and later production gates.
    Optional auth_policy enables cookie auth and request-bound management routes,
    blocking all unbound APIs. PG Run leases and expiry-only cleanup are enabled;
    Proxy client IPs require explicit secure API-role assembly; the standalone
    server still binds loopback and trusts only its same-host HTTPS proxy.
    No queue Worker, model replay or migrations run at startup.
    """
    if metrics_policy is not None:
        from app.private_metrics import MetricsPolicy
        if not isinstance(metrics_policy, MetricsPolicy):
            raise ValueError('Explicit MetricsPolicy required')
    if auth_policy is not None and (repositories is None or repositories.engine.dialect.name != "postgresql"):
        raise ValueError("Cookie authentication requires explicit PostgreSQL repositories")
    if type(allow_authenticated_proxy_clients) is not bool:
        raise ValueError('Explicit boolean proxy-client policy required')
    if allow_authenticated_proxy_clients and (auth_policy is None or not auth_policy.secure_cookie
            or not repositories.strict_role or repositories.runtime_role!='api'):
        raise ValueError('Proxy clients require secure authenticated strict API-role assembly')
    if (rag_embedding_gateway is not None or rag_reranker is not None) and auth_policy is None:
        raise ValueError('Hybrid RAG requires authenticated PostgreSQL')
    if (rag_embedding_gateway is None) != (rag_reranker is None):
        raise ValueError('Hybrid RAG requires both local models')
    if auth_policy is not None and repositories.strict_role and repositories.runtime_role != 'api':
        raise ValueError('Authenticated API requires the API database role')
    if durable_chat and auth_policy is None:
        raise ValueError('Durable chat requires authenticated PostgreSQL')
    if durable_knowledge and auth_policy is None:
        raise ValueError('Durable knowledge requires authenticated PostgreSQL')
    if event_retention_seconds is not None:
        from app.persistence.durable_chat_repository import validate_retention
        validate_retention(event_retention_seconds)
        if not durable_chat:
            raise ValueError('Event retention requires durable chat')
    secrets = secret_store or SecretStore()
    if storage_policy is not None and (auth_policy is None or (knowledge_paths or {}).get('object_store') is not None):
        raise ValueError('Storage policy requires authentication and cannot mix with direct store injection')
    from app.storage_config import StorageConfiguration, StorageConflict
    storage_configuration = StorageConfiguration(repositories.engine,secrets,storage_policy) if auth_policy else None
    gateway = ModelGateway(secrets, repository=repositories.config if repositories else None,
                           local_endpoint=local_model_endpoint)
    prompt_service = PromptService(repositories.config if repositories else None)
    admin_service = AdminService(repositories.admin if repositories else None)
    knowledge = KnowledgeService(repository=repositories.knowledge if repositories else None,
        embedding_gateway=rag_embedding_gateway, reranker=rag_reranker, **(knowledge_paths or {}))
    knowledge_jobs = KnowledgeJobService(knowledge, allow_local_worker=repositories is None)
    chat = ChatOrchestrator(gateway, knowledge, repository=repositories.chat if repositories else None,
                            prompt_resolver=prompt_service.runtime_prompt)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if repositories:
            await asyncio.to_thread(repositories.check_schema if auth_policy else repositories.initialize)
        else:
            await asyncio.to_thread(init_database)
            await asyncio.to_thread(recover_interrupted_runs)
        if auth_policy is None:
            await asyncio.to_thread(knowledge.ensure_index_current)
        if repositories is None:
            await asyncio.to_thread(knowledge_jobs.recover)
        reaper_stop = asyncio.Event()
        reaper = asyncio.create_task(run_lease_reaper(repositories.chat, reaper_stop)) if repositories else None
        if durable_chat:
            from app.persistence.durable_chat_repository import DurableChatRepository
            async def queue_reaper():
                while not reaper_stop.is_set():
                    try:
                        await asyncio.to_thread(DurableChatRepository(repositories.engine).reap_pending)
                        if event_retention_seconds is not None:
                            await asyncio.to_thread(DurableChatRepository(repositories.engine).prune_events,
                                retention_seconds=event_retention_seconds)
                    except Exception:
                        pass  # Database unavailable; cannot invent an in-memory terminal result.
                    try:
                        await asyncio.wait_for(reaper_stop.wait(),timeout=5)
                    except asyncio.TimeoutError:
                        pass
            pending_reaper = asyncio.create_task(queue_reaper())
        else:
            pending_reaper = None
        if durable_knowledge:
            from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
            async def import_reaper():
                while not reaper_stop.is_set():
                    try:
                        await asyncio.to_thread(DurableKnowledgeRepository(repositories.engine).reap_expired)
                    except Exception:
                        pass  # DB failure cannot authorize parsing or invent progress.
                    try:
                        await asyncio.wait_for(reaper_stop.wait(),timeout=5)
                    except asyncio.TimeoutError:
                        pass
            knowledge_reaper = asyncio.create_task(import_reaper())
        else:
            knowledge_reaper = None
        try:
            async with AsyncExitStack() as observability_resources:
                if metrics_policy is not None:
                    from app.private_metrics import private_metrics_listener
                    app.state.private_metrics_port = await observability_resources.enter_async_context(
                        private_metrics_listener(app.state.http_telemetry, metrics_policy))
                try:
                    yield
                finally:
                    app.state.private_metrics_port = None
        finally:
            await chat.shutdown()
            reaper_stop.set()
            if reaper is not None:
                await reaper
            if pending_reaper is not None:
                await pending_reaper
            if knowledge_reaper is not None:
                await knowledge_reaper
            await asyncio.to_thread(knowledge_jobs.close)
            await gateway.close()
            if repositories:
                repositories.close()

    app = ObservedFastAPI(title="AI 客服", version="0.1.0", lifespan=lifespan, telemetry=http_telemetry, tracing=tracing)
    if repositories is not None and tracing is not None:
        install_database_tracing(repositories.engine)
    if repositories is not None and metrics_policy is not None:
        from app.model_budget_metrics import ModelBudgetMetrics
        from app.model_circuit_metrics import ModelCircuitMetrics
        app.state.http_telemetry.budget_metrics = ModelBudgetMetrics(repositories.engine)
        app.state.http_telemetry.circuit_metrics = ModelCircuitMetrics(repositories.engine)
        from app.local_inference_metrics import LocalInferenceMetrics
        app.state.http_telemetry.local_inference_metrics = LocalInferenceMetrics(repositories.engine)
    app.state.private_metrics_port = None

    @app.exception_handler(ObjectStoreError)
    async def private_object_failure(request: Request, exc: ObjectStoreError):
        return JSONResponse(status_code=503, content={'detail':'Private object storage unavailable', 'code':'object_store_unavailable'})
    app.add_middleware(CORSMiddleware, allow_origins=[auth_policy.origin] if auth_policy else ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=auth_policy is not None, allow_methods=["*"], allow_headers=["*"])
    app.state.chat = chat
    app.state.gateway = gateway
    app.state.knowledge = knowledge
    app.state.knowledge_jobs = knowledge_jobs
    app.state.admin = admin_service
    app.state.prompts = prompt_service

    @app.get("/api/auth/config")
    def auth_configuration():
        return JSONResponse({"enabled": auth_policy is not None}, headers={"Cache-Control": "no-store"})
    def request_admin(request: Request):
        return AdminService(repositories.admin.authenticated(request.state.login_token)) if auth_policy else admin_service

    def request_gateway(request: Request):
        return ModelGateway(secrets, repository=repositories.config.authenticated(request.state.login_token),
            client_factory=gateway._client, local_endpoint=local_model_endpoint,
            local_client_factory=gateway._local_client) if auth_policy else gateway

    def request_prompts(request: Request):
        return PromptService(repositories.config.authenticated(request.state.login_token)) if auth_policy else prompt_service

    def request_budget(request: Request):
        if auth_policy is None:
            raise HTTPException(409,'Model budgets require authenticated PostgreSQL')
        return repositories.config.authenticated(request.state.login_token).model_budgets()

    def request_fallback(request: Request):
        if auth_policy is None:
            raise HTTPException(409, 'Online fallback consent requires authenticated PostgreSQL')
        return repositories.config.authenticated(request.state.login_token).model_fallbacks()

    def request_knowledge(request: Request):
        if not auth_policy:
            yield knowledge
            return
        paths = dict(knowledge_paths or {})
        owned_store = None
        if 'object_store' not in paths:
            owned_store = storage_configuration.resolve(request.state.login_token)
            paths['object_store'] = owned_store
        try:
            yield KnowledgeService(repository=repositories.knowledge.authenticated(request.state.login_token),
                embedding_gateway=rag_embedding_gateway, reranker=rag_reranker, **paths)
        finally:
            if owned_store:
                owned_store.close()

    @app.get('/api/admin/storage')
    def storage_settings(request: Request):
        if storage_configuration is None:
            return {'enabled':False,'configured':False,'target':None,'revision':0,'targets':[], 'policy_configured':False}
        return storage_configuration.describe(request.state.login_token)

    @app.put('/api/admin/storage')
    def update_storage_settings(request: Request, payload: dict):
        if storage_configuration is None:
            raise HTTPException(409,'Storage configuration requires authenticated PostgreSQL')
        allowed = {'target','revision','access_key','secret_key','session_token'}
        if set(payload) - allowed or not all(isinstance(payload.get(name),str) and 0 < len(payload[name]) <= 4096 for name in ('target','access_key','secret_key')):
            raise HTTPException(422,'Invalid storage configuration')
        session_token = payload.get('session_token')
        if session_token is not None and (not isinstance(session_token,str) or len(session_token) > 4096):
            raise HTTPException(422,'Invalid storage configuration')
        try:
            return storage_configuration.save(request.state.login_token,target_name=payload['target'],
                expected_revision=payload.get('revision'),access_key=payload['access_key'],secret_key=payload['secret_key'],session_token=session_token)
        except StorageConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except ValueError:
            raise HTTPException(422,'Invalid storage configuration') from None

    def request_knowledge_jobs(knowledge: KnowledgeService = Depends(request_knowledge)):
        return KnowledgeJobService(knowledge, allow_local_worker=False,durable=durable_knowledge) if auth_policy else knowledge_jobs

    def request_chat(request: Request, gateway: ModelGateway = Depends(request_gateway),
            knowledge: KnowledgeService = Depends(request_knowledge), prompt_service: PromptService = Depends(request_prompts)):
        if not auth_policy:
            return chat
        token = request.state.login_token
        return ChatOrchestrator(gateway, knowledge, repository=repositories.chat.authenticated(token),
            prompt_resolver=prompt_service.runtime_prompt, runtime=chat.runtime,
            max_concurrent_runs=chat.max_concurrent_runs, queue_timeout=chat.queue_timeout,
            run_timeout=chat.run_timeout, heartbeat_interval=chat.heartbeat_interval,
            identity_resolver=lambda: repositories.identity.resolve(token))
    if auth_policy is not None:
        from app.auth_http import install_auth
        install_auth(app, repositories, auth_policy)

        @app.post("/api/admin/setup")
        def initialize_tenant(prompt_service: PromptService = Depends(request_prompts), gateway: ModelGateway = Depends(request_gateway)):
            prompt_service.repository.initialize_tenant(utc_now())
            return {"providers": gateway.list_configs(), "prompts": prompt_service.list_prompts()}

    @app.middleware("http")
    async def local_requests_only(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if not allow_authenticated_proxy_clients and client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            return JSONResponse({"detail": "管理与客服服务仅允许从本机访问"}, status_code=403)
        return await call_next(request)


    @app.get("/health")
    def health():
        database_status = repositories.health() if repositories else local_database_health()
        return {
            "status": "ok", "database": True, **database_status,
            "master_key": secrets.path.exists(), "model_runs": chat.capacity(),
        }


    @app.get("/health/live")
    def liveness():
        return {"status": "ok"}


    @app.get("/health/ready")
    def readiness():
        try:
            repositories.health() if repositories else local_database_health()
        except Exception:
            return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
        return {"status": "ready", "database": True, "model_runs": chat.capacity()}


    @app.post("/api/chat/sessions")
    def create_session(chat: ChatOrchestrator = Depends(request_chat)):
        return chat.create_session()


    @app.get("/api/chat/sessions")
    def list_sessions(limit: int = Query(50, ge=1, le=100), scope: str = Query("active", pattern="^(active|archived)$"), chat: ChatOrchestrator = Depends(request_chat)):
        return chat.list_sessions(limit, scope)


    @app.get("/api/chat/sessions/{session_id}")
    def get_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat)):
        if durable_chat:
            from app.persistence.durable_chat_repository import DurableChatRepository,DurableChatConflict
            try:
                result = DurableChatRepository(repositories.engine,token=chat.repository._token).snapshot(session_id)
            except DurableChatConflict:
                raise HTTPException(503,'运行快照暂不可用') from None
            if result is None:
                raise HTTPException(404,'会话不存在')
            return result
        session = chat.session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        return {"session": session, "messages": chat.public_history(session_id)}


    @app.patch("/api/chat/sessions/{session_id}")
    def rename_session(session_id: str, payload: SessionTitleUpdate, chat: ChatOrchestrator = Depends(request_chat)):
        session = chat.rename_session(session_id, payload.title)
        if not session:
            raise HTTPException(404, "会话不存在")
        return session


    @app.post("/api/chat/sessions/{session_id}/archive")
    def archive_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat)):
        session = chat.archive_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        return session


    @app.post("/api/chat/sessions/{session_id}/restore")
    def restore_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat)):
        session = chat.restore_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        return session


    @app.delete("/api/chat/sessions/{session_id}")
    def delete_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat)):
        if not chat.delete_session(session_id):
            raise HTTPException(404, "会话不存在")
        return {"deleted": True, "recoverable": True}


    @app.post("/api/chat/sessions/{session_id}/messages")
    def send_message(session_id: str, payload: ChatMessageInput, chat: ChatOrchestrator = Depends(request_chat)):
        if not chat.repository.can_start_session(session_id):
            raise HTTPException(404, "会话不存在")
        session = chat.session(session_id, include_deleted=True)
        if session and session["deleted_at"]:
            raise HTTPException(404, "会话不存在")
        if session and session["archived_at"]:
            raise HTTPException(409, "已归档的会话不能继续发送消息，请先恢复")
        if durable_chat:
            from app.chat import make_session_title
            from app.persistence.durable_chat_repository import DurableChatRepository,DurableChatConflict
            queue = DurableChatRepository(repositories.engine,token=chat.repository._token)
            selected = chat.gateway.repository.active_provider()
            prompt_id,instructions = chat.prompt_resolver()
            rid = str(payload.request_id or uuid.uuid4())
            try:
                queue.enqueue(session_id=session_id,request_id=rid,content=payload.content,title=make_session_title(payload.content),
                    provider=selected['provider'] if selected else 'deepseek',model=selected['model'] if selected else 'deepseek-chat',
                    prompt_version_id=prompt_id,instructions=instructions,
                    trace_context=capture_carrier())
            except DurableChatConflict:
                raise HTTPException(409,'请求编号或会话状态冲突') from None
            return JSONResponse({'run_id':rid,'events_url':f'/api/chat/sessions/{session_id}/runs/{rid}/events'},status_code=202)
        return StreamingResponse(
            chat.stream(session_id, payload.content),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get('/api/chat/sessions/{session_id}/runs/{run_id}/events')
    async def follow_run(session_id:str,run_id:str,request:Request,after:int=Query(0,ge=0,le=1000000),chat:ChatOrchestrator=Depends(request_chat)):
        if not durable_chat:
            raise HTTPException(404,'持久事件未启用')
        from app.persistence.durable_chat_repository import DurableChatRepository,DurableChatConflict,EventCursorExpired
        from app.chat import sse
        queue = DurableChatRepository(repositories.engine,token=chat.repository._token)
        cursor = request.headers.get('last-event-id',str(after))
        if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 7:
            raise HTTPException(400,'事件游标无效')
        cursor = int(cursor)
        try:
            initial = await asyncio.to_thread(queue.read_events,session_id,run_id,after=cursor)
        except EventCursorExpired:
            return JSONResponse({'code':'cursor_expired','detail':'事件游标已过期，请读取运行快照',
                'snapshot_url':f'/api/chat/sessions/{session_id}/runs/{run_id}'},status_code=409)
        except (ValueError,DurableChatConflict):
            raise HTTPException(409,'事件游标无效') from None
        if initial is None or not chat.session(session_id):
            raise HTTPException(404,'运行不存在')
        async def events():
            current = cursor
            heartbeat = asyncio.get_running_loop().time()
            try:
                while True:
                    if await request.is_disconnected():
                        return  # Subscriber disconnect never cancels the Worker.
                    result = await asyncio.to_thread(queue.read_events,session_id,run_id,after=current)
                    if result is None:
                        return
                    for item in result['events']:
                        current = item['sequence']
                        yield f'id: {current}\n' + sse(item['event'],item['data'])
                    if result['status'] not in {'queued','running'} and current >= result['sequence']:
                        return
                    if asyncio.get_running_loop().time()-heartbeat >= 5:
                        yield sse('ping',{'timestamp':utc_now()})
                        heartbeat = asyncio.get_running_loop().time()
                    await asyncio.sleep(0.25)
            except EventCursorExpired:
                yield sse('cursor_expired',{})
            except (AuthenticationError,AuthorizationError):
                yield sse('answer_reset',{})
                yield sse('error',{'code':'authentication_required','message':'登录身份已失效，请重新登录'})
        return StreamingResponse(events(),media_type='text/event-stream',headers={
            'Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no'})

    @app.get('/api/chat/sessions/{session_id}/runs/{run_id}')
    def run_snapshot(session_id:str,run_id:str,chat:ChatOrchestrator=Depends(request_chat)):
        if not durable_chat:
            raise HTTPException(404,'持久运行未启用')
        from app.persistence.durable_chat_repository import DurableChatRepository,DurableChatConflict
        try:
            result = DurableChatRepository(repositories.engine,token=chat.repository._token).snapshot(session_id,run_id)
        except DurableChatConflict:
            raise HTTPException(503,'运行快照暂不可用') from None
        except ValueError:
            raise HTTPException(404,'运行不存在') from None
        if result is None:
            raise HTTPException(404,'运行不存在')
        return result


    @app.put("/api/chat/sessions/{session_id}/messages/{message_id}/feedback")
    def update_message_feedback(session_id: str, message_id: str, payload: MessageFeedbackInput, chat: ChatOrchestrator = Depends(request_chat)):
        result = chat.set_message_feedback(session_id, message_id, payload.rating)
        if not result:
            raise HTTPException(404, "可评价的客服回答不存在")
        return result


    @app.post("/api/chat/sessions/{session_id}/runs/{run_id}/cancel")
    def cancel_run(session_id: str, run_id: str, chat: ChatOrchestrator = Depends(request_chat)):
        return {"cancelled": chat.cancel(session_id, run_id)}


    @app.get("/api/admin/providers")
    def list_providers(gateway: ModelGateway = Depends(request_gateway)):
        return gateway.list_configs()


    @app.get('/api/admin/model-budget')
    def model_budget_status(budget=Depends(request_budget)):
        return budget.status()

    @app.get('/api/admin/model-fallback/{provider}')
    def model_fallback_status(provider: str, fallback=Depends(request_fallback)):
        try:
            return fallback.status(provider)
        except ValueError:
            raise HTTPException(422, 'Invalid online fallback provider') from None

    @app.put('/api/admin/model-fallback/{provider}')
    def publish_model_fallback(provider: str, payload: ModelFallbackConsentPublish,
                               fallback=Depends(request_fallback)):
        from app.persistence.model_cost_repository import CostConflict
        try:
            return fallback.publish(provider=provider, **payload.model_dump(mode='json'))
        except CostConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError:
            raise HTTPException(422, 'Invalid online fallback consent') from None


    @app.get('/api/admin/model-prices/{provider}')
    def model_price_status(provider: str, model: str = Query(min_length=1,max_length=200), budget=Depends(request_budget)):
        if provider not in {'deepseek','openai','local'}:
            raise HTTPException(422,'Invalid model price identity')
        try:
            return {'price':budget.current_price(provider=provider,model=model)}
        except ValueError:
            raise HTTPException(422,'Invalid model price identity') from None


    @app.put('/api/admin/model-prices/{provider}')
    def publish_model_price(provider: str, payload: ModelPricePublish, budget=Depends(request_budget)):
        if provider not in {'deepseek','openai','local'}:
            raise HTTPException(422,'Invalid model price identity')
        from app.persistence.model_cost_repository import CostConflict
        try:
            row=budget.publish_price(provider=provider,**payload.model_dump(mode='json'))
            for name in ('input_rate','output_rate','cache_read_rate','cache_write_rate'):
                row[name]=format(row[name],'.9f')
            return row
        except CostConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except ValueError:
            raise HTTPException(422,'Invalid model price specification') from None


    @app.put('/api/admin/model-budget')
    def publish_model_budget(payload: ModelBudgetPublish, budget=Depends(request_budget)):
        from app.persistence.model_cost_repository import CostConflict
        try:
            row=budget.publish(**payload.model_dump(mode='json'))
            for name in ('tenant_limit','day_limit','run_limit'):
                row[name]=format(row[name],'.9f')
            return row
        except CostConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except ValueError:
            raise HTTPException(422,'Invalid model budget specification') from None

    @app.get('/api/admin/model-route-budget/{provider}')
    def model_route_budget_status(provider: str, model: str = Query(min_length=1,max_length=200),
                                  budget=Depends(request_budget)):
        try:
            return {'policy':budget.route_policy(provider=provider,model=model)}
        except ValueError:
            raise HTTPException(422,'Invalid route budget identity') from None

    @app.put('/api/admin/model-route-budget/{provider}')
    def publish_model_route_budget(provider: str, payload: ModelRouteBudgetPublish,
                                   budget=Depends(request_budget)):
        from app.persistence.model_cost_repository import CostConflict
        try:
            return budget.publish_route_policy(provider=provider,**payload.model_dump(mode='json'))
        except CostConflict as exc:
            raise HTTPException(409,str(exc)) from None
        except ValueError:
            raise HTTPException(422,'Invalid route budget policy') from None


    @app.get("/api/admin/chat/sessions")
    def admin_sessions(
        lifecycle: str = Query("all", pattern="^(active|archived|deleted|all)$"),
        scope: str | None = Query(None, pattern="^(active|archived|deleted|all)$"),
        q: str | None = None,
        management_status: str | None = Query(None, pattern="^(open|pending|resolved)$"),
        assignee: str | None = None,
        tag: str | None = None,
        provider: str | None = None,
        run_status: str | None = Query(None, pattern="^(running|complete|error|interrupted)$"),
        has_contact: bool | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=100),
        admin_service: AdminService = Depends(request_admin),
    ):
        filters = {"lifecycle": scope or lifecycle, "q": q, "management_status": management_status, "assignee": assignee, "tag": tag, "provider": provider, "run_status": run_status, "has_contact": has_contact, "date_from": date_from, "date_to": date_to}
        return admin_service.list_sessions(filters, page, page_size)


    @app.get("/api/admin/chat/sessions/{session_id}")
    def admin_session_detail(session_id: str, admin_service: AdminService = Depends(request_admin)):
        detail = admin_service.session_detail(session_id)
        if not detail:
            raise HTTPException(404, "会话不存在")
        admin_service.audit("session.view", "session", session_id, "查看会话详情")
        return detail


    @app.patch("/api/admin/chat/sessions/{session_id}")
    def admin_update_session(session_id: str, payload: AdminSessionUpdate, admin_service: AdminService = Depends(request_admin)):
        detail = admin_service.update_session(session_id, payload.model_dump(exclude_unset=True))
        if not detail:
            raise HTTPException(404, "会话不存在")
        admin_service.audit("session.update", "session", session_id, "更新会话管理信息", {"fields": list(payload.model_fields_set)})
        return detail


    @app.post("/api/admin/chat/sessions/{session_id}/notes")
    def admin_add_session_note(session_id: str, payload: SessionNoteInput, admin_service: AdminService = Depends(request_admin)):
        note = admin_service.add_note(session_id, payload.content)
        if not note:
            raise HTTPException(404, "会话不存在")
        return note


    @app.get("/api/admin/chat/sessions/{session_id}/export")
    def admin_export_session(session_id: str, format: str = Query("markdown", pattern="^(markdown|csv)$"), admin_service: AdminService = Depends(request_admin)):
        detail = admin_service.session_detail(session_id)
        if not detail:
            raise HTTPException(404, "会话不存在")
        content, media_type, filename = admin_service.export_session(detail, format)
        admin_service.audit("session.export", "session", session_id, f"导出会话 {format}")
        return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


    @app.post("/api/admin/chat/sessions/{session_id}/archive")
    def admin_archive_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat), admin_service: AdminService = Depends(request_admin)):
        session = chat.archive_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        admin_service.audit("session.archive", "session", session_id, "归档会话")
        return session


    @app.post("/api/admin/chat/sessions/{session_id}/restore")
    def admin_restore_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat), admin_service: AdminService = Depends(request_admin)):
        session = chat.restore_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        admin_service.audit("session.restore", "session", session_id, "恢复会话")
        return session


    @app.delete("/api/admin/chat/sessions/{session_id}")
    def admin_delete_session(session_id: str, chat: ChatOrchestrator = Depends(request_chat), admin_service: AdminService = Depends(request_admin)):
        if not chat.delete_session(session_id):
            raise HTTPException(404, "会话不存在")
        admin_service.audit("session.delete", "session", session_id, "安全删除会话")
        return {"deleted": True, "recoverable": True}


    @app.get("/api/admin/overview")
    def admin_overview(admin_service: AdminService = Depends(request_admin)):
        return admin_service.overview()


    @app.get("/api/admin/audit")
    def admin_audit(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), action: str | None = None, admin_service: AdminService = Depends(request_admin)):
        return admin_service.list_audit(page, page_size, action)


    @app.get("/api/admin/prompts")
    def admin_prompts(prompt_service: PromptService = Depends(request_prompts)):
        return prompt_service.list_prompts()


    @app.post("/api/admin/prompts/drafts")
    def admin_prompt_draft(payload: PromptDraftInput, prompt_service: PromptService = Depends(request_prompts), admin_service: AdminService = Depends(request_admin)):
        draft = prompt_service.create_draft(payload.content, payload.change_note)
        admin_service.audit("prompt.draft", "prompt", draft["id"], f"创建提示词草稿 v{draft['version']}")
        return draft


    @app.post("/api/admin/prompts/{prompt_id}/publish")
    def admin_prompt_publish(prompt_id: str, prompt_service: PromptService = Depends(request_prompts), admin_service: AdminService = Depends(request_admin)):
        published = prompt_service.publish(prompt_id)
        if not published:
            raise HTTPException(404, "提示词版本不存在")
        admin_service.audit("prompt.publish", "prompt", prompt_id, f"发布提示词 v{published['version']}")
        return published


    @app.post("/api/admin/prompts/{prompt_id}/rollback")
    def admin_prompt_rollback(prompt_id: str, payload: PromptRollbackInput, prompt_service: PromptService = Depends(request_prompts), admin_service: AdminService = Depends(request_admin)):
        published = prompt_service.rollback(prompt_id, payload.change_note)
        if not published:
            raise HTTPException(404, "提示词版本不存在")
        admin_service.audit("prompt.rollback", "prompt", published["id"], f"回滚并发布为 v{published['version']}", {"source_id": prompt_id})
        return published


    @app.put("/api/admin/providers/{provider}")
    def update_provider(provider: str, payload: ProviderUpdate, gateway: ModelGateway = Depends(request_gateway), admin_service: AdminService = Depends(request_admin)):
        try:
            gateway.update(provider, payload.model, payload.api_key)
            result = gateway.list_configs()
            admin_service.audit("provider.update", "provider", provider, "更新模型配置", {"model": payload.model, "key_changed": bool(payload.api_key)})
            return result
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc


    @app.delete("/api/admin/providers/{provider}/key")
    def delete_provider_key(provider: str, gateway: ModelGateway = Depends(request_gateway), admin_service: AdminService = Depends(request_admin)):
        try:
            gateway.delete_key(provider)
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        admin_service.audit("provider.key_delete", "provider", provider, "删除模型密钥")
        return gateway.list_configs()


    @app.post("/api/admin/providers/{provider}/test")
    async def test_provider(provider: str, payload: ProviderTest, gateway: ModelGateway = Depends(request_gateway)):
        try:
            return await gateway.test(provider, payload.model, payload.api_key)
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc


    @app.put("/api/admin/active-provider")
    def activate_provider(payload: ActiveProviderUpdate, gateway: ModelGateway = Depends(request_gateway), admin_service: AdminService = Depends(request_admin)):
        try:
            gateway.activate(payload.provider)
            result = gateway.list_configs()
            admin_service.audit("provider.activate", "provider", payload.provider, "切换当前模型供应商")
            return result
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc


    @app.get("/api/admin/documents")
    def list_documents(knowledge: KnowledgeService = Depends(request_knowledge)):
        return knowledge.list_documents()


    @app.get("/api/knowledge/assets/{asset_id}")
    def knowledge_asset(asset_id: str, knowledge: KnowledgeService = Depends(request_knowledge)):
        try:
            content, media_type = knowledge.asset_content(asset_id)
        except KnowledgeError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ObjectStoreError:
            raise HTTPException(503, 'Private object storage unavailable') from None
        return Response(
            content,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'inline; filename="knowledge-image.png"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )


    @app.get("/api/admin/knowledge/location")
    def knowledge_location(knowledge: KnowledgeService = Depends(request_knowledge)):
        return knowledge.location()


    @app.post("/api/admin/knowledge/import")
    def import_knowledge_path(payload: KnowledgePathInput, knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin)):
        try:
            result = knowledge.import_path(payload.path, payload.recursive)
            admin_service.audit("knowledge.import", "knowledge", None, "从本机路径导入知识文档", {"imported_count": result["imported_count"]})
            return result
        except KnowledgeError as exc:
            raise HTTPException(400, str(exc)) from exc


    @app.post("/api/admin/knowledge/jobs", status_code=202)
    def create_knowledge_job(payload: KnowledgePathInput, knowledge_jobs: KnowledgeJobService = Depends(request_knowledge_jobs), admin_service: AdminService = Depends(request_admin)):
        try:
            job = knowledge_jobs.create(payload.path, payload.recursive)
            admin_service.audit("knowledge.job.create", "knowledge_job", job["id"], "创建知识库导入任务", {"source_path": job["source_path"]})
            return job
        except KnowledgeError as exc:
            raise HTTPException(400, str(exc)) from exc


    @app.get("/api/admin/knowledge/jobs")
    def list_knowledge_jobs(limit: int = Query(20, ge=1, le=100), knowledge_jobs: KnowledgeJobService = Depends(request_knowledge_jobs)):
        return knowledge_jobs.list(limit)


    @app.post("/api/admin/knowledge/jobs/{job_id}/cancel")
    def cancel_knowledge_job(job_id: str, knowledge_jobs: KnowledgeJobService = Depends(request_knowledge_jobs)):
        try:
            return knowledge_jobs.cancel(job_id)
        except KnowledgeError as exc:
            raise HTTPException(400, str(exc)) from exc


    @app.get("/api/admin/knowledge/jobs/{job_id}")
    def get_knowledge_job(job_id: str, knowledge_jobs: KnowledgeJobService = Depends(request_knowledge_jobs)):
        try:
            return knowledge_jobs.get(job_id)
        except KnowledgeError as exc:
            raise HTTPException(404, str(exc)) from exc


    @app.post("/api/admin/documents")
    async def upload_document(file: UploadFile = File(...), knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin), jobs: KnowledgeJobService = Depends(request_knowledge_jobs)):
        try:
            data = bytearray()
            while chunk := await file.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            if durable_knowledge:
                job = await asyncio.to_thread(jobs.create_upload,file.filename or 'document.txt',bytes(data))
                return JSONResponse(job,status_code=202)
            result = await asyncio.to_thread(knowledge.ingest, file.filename or "document.txt", bytes(data))
            await asyncio.to_thread(admin_service.audit, "knowledge.upload", "document", result["id"], "上传知识文档", {"display_name": result["display_name"]})
            return result
        except KnowledgeDuplicateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KnowledgeError as exc:
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except (AuthenticationError, AuthorizationError):
            raise
        except Exception as exc:
            raise HTTPException(400, "文档解析失败") from exc
        finally:
            await file.close()


    @app.delete("/api/admin/documents/{document_id}")
    def delete_document(document_id: str, knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin)):
        try:
            knowledge.delete(document_id)
            admin_service.audit("knowledge.delete", "document", document_id, "删除知识文档")
            return {"deleted": True}
        except KnowledgeError as exc:
            raise HTTPException(404, str(exc)) from exc


    @app.post("/api/admin/documents/{document_id}/reparse")
    def reparse_document(document_id: str, knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin), jobs: KnowledgeJobService = Depends(request_knowledge_jobs)):
        try:
            if durable_knowledge:
                return JSONResponse(jobs.create_reparse(document_id),status_code=202)
            result = knowledge.reparse(document_id)
            admin_service.audit("knowledge.reparse", "document", document_id, "重新解析知识文档", {"version": result["version"]})
            return result
        except KnowledgeError as exc:
            raise HTTPException(400, str(exc)) from exc


    @app.post("/api/admin/index/rebuild")
    def rebuild_index(knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin)):
        result = {"indexed_chunks": knowledge.rebuild()}
        admin_service.audit("knowledge.rebuild", "knowledge", None, "重建知识库索引", result)
        return result


    @app.get("/api/admin/documents/{document_id}")
    def document_detail(document_id: str, knowledge: KnowledgeService = Depends(request_knowledge)):
        document = knowledge.repository.document(document_id)
        if not document:
            raise HTTPException(404, "文档不存在")
        chunks = knowledge.repository.document_chunks(document_id)
        document_value = dict(document)
        document_value["warnings"] = decode_json(document_value.pop("warnings_json", []), [])
        document_value["metadata"] = decode_json(document_value.pop("metadata_json", {}), {})
        document_value['metadata'].pop('_private_object', None)
        chunk_values = []
        for row in chunks:
            value = dict(row)
            value["metadata"] = decode_json(value.pop("metadata_json", {}), {})
            chunk_values.append(value)
        return {"document": document_value, "chunks": chunk_values, "assets": knowledge.list_assets(document_id)}


    @app.post("/api/admin/knowledge/search-test")
    def knowledge_search_test(payload: KnowledgeSearchInput, knowledge: KnowledgeService = Depends(request_knowledge)):
        return {"items": knowledge.search(payload.query, payload.limit)}


    @app.get("/api/admin/knowledge/terms")
    def knowledge_terms(knowledge: KnowledgeService = Depends(request_knowledge)):
        return {"defaults": DEFAULT_SYNONYM_GROUPS, "custom": custom_synonym_groups(repository=knowledge.repository), "all": all_synonym_groups(repository=knowledge.repository)}


    @app.put("/api/admin/knowledge/terms")
    def update_knowledge_terms(payload: KnowledgeTermsUpdate, knowledge: KnowledgeService = Depends(request_knowledge), admin_service: AdminService = Depends(request_admin)):
        groups = save_custom_synonym_groups(payload.groups, repository=knowledge.repository)
        admin_service.audit("knowledge.terms.update", "knowledge", None, "更新知识库同义词", {"group_count": len(groups)})
        return {"defaults": DEFAULT_SYNONYM_GROUPS, "custom": groups, "all": all_synonym_groups(repository=knowledge.repository)}


    @app.post("/api/contacts")
    def create_contact(payload: ContactInput, admin_service: AdminService = Depends(request_admin)):
        contact_id = admin_service.repository.create_contact(session_id=payload.session_id, name=payload.name, phone=payload.phone,
                                                             email=str(payload.email) if payload.email else None, summary=payload.summary, now=utc_now())
        if contact_id is None:
            raise HTTPException(404, "会话不存在")
        return {"id": contact_id, "saved": True}


    @app.get("/api/admin/contacts")
    def list_contacts(q: str | None = None, status: str | None = Query(None, pattern="^(new|following|closed)$"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), admin_service: AdminService = Depends(request_admin)):
        return admin_service.repository.list_contacts(q, status, page, page_size)


    @app.patch("/api/admin/contacts/{contact_id}")
    def update_contact(contact_id: str, payload: ContactAdminUpdate, admin_service: AdminService = Depends(request_admin)):
        values = payload.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(400, "没有可更新字段")
        row = admin_service.repository.update_contact(contact_id, values)
        if row is None:
            raise HTTPException(404, "联系记录不存在")
        return dict(row)


    @app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    def unknown_api(path: str = ""):
        target = f"/api/{path}" if path else "/api"
        raise HTTPException(404, f"API 接口不存在: {target}")


    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        def frontend(path: str):
            target = (FRONTEND_DIST / path).resolve()
            if FRONTEND_DIST.resolve() not in target.parents and target != FRONTEND_DIST.resolve():
                raise HTTPException(404, "文件不存在")
            if path and target.is_file():
                return FileResponse(target, headers={"Cache-Control": "no-store"} if target.suffix == ".html" else None)
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-store"})

    if repositories:
        from app.recovery import check_operational, RecoveryHold
        from app.maintenance import MaintenanceHold

        @app.middleware('http')
        async def recovery_gate(request: Request, call_next):
            try:
                await asyncio.to_thread(check_operational, repositories.engine)
            except RecoveryHold:
                return JSONResponse({'code': 'recovery_hold', 'detail': '恢复数据库尚未完成授权对账，服务保持停用'},
                                    status_code=503, headers={'Cache-Control': 'no-store'})
            except MaintenanceHold:
                return JSONResponse({'code':'maintenance_hold','detail':'服务正在进行受控维护，请稍后刷新；不要重复提交操作'},
                                    status_code=503,headers={'Cache-Control':'no-store'})
            return await call_next(request)

    return app


# Compatibility aliases refer only to the default local application instance.
# Constructing it performs no database or secret reads.
app = create_app()
chat = app.state.chat
gateway = app.state.gateway
knowledge = app.state.knowledge
knowledge_jobs = app.state.knowledge_jobs
