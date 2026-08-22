import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.chat import ChatOrchestrator
from app import admin as admin_service
from app.database import connect, init_database, recover_interrupted_runs, utc_now
from app.knowledge import KnowledgeDuplicateError, KnowledgeError, KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_terms import DEFAULT_SYNONYM_GROUPS, all_synonym_groups, custom_synonym_groups, save_custom_synonym_groups
from app.providers import ModelGateway, ProviderError
from app.schemas import ActiveProviderUpdate, AdminSessionUpdate, ChatMessageInput, ContactAdminUpdate, ContactInput, KnowledgePathInput, KnowledgeSearchInput, KnowledgeTermsUpdate, MessageFeedbackInput, PromptDraftInput, PromptRollbackInput, ProviderTest, ProviderUpdate, SessionNoteInput, SessionTitleUpdate
from app import prompts as prompt_service
from app.security import SecretStore
from app.settings import FRONTEND_DIST, MASTER_KEY_PATH, MAX_UPLOAD_BYTES


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    recover_interrupted_runs()
    knowledge.ensure_index_current()
    knowledge_jobs.recover()
    try:
        yield
    finally:
        await chat.shutdown()
        await gateway.close()


app = FastAPI(title="AI 客服", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
secrets = SecretStore()
gateway = ModelGateway(secrets)
knowledge = KnowledgeService()
knowledge_jobs = KnowledgeJobService(knowledge)
chat = ChatOrchestrator(gateway, knowledge)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return JSONResponse({"detail": "管理与客服服务仅允许从本机访问"}, status_code=403)
    return await call_next(request)


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1").fetchone()
        journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    return {
        "status": "ok",
        "database": True,
        "database_journal_mode": journal_mode,
        "master_key": MASTER_KEY_PATH.exists(),
        "model_runs": chat.capacity(),
    }


@app.get("/health/live")
def liveness():
    return {"status": "ok"}


@app.get("/health/ready")
def readiness():
    try:
        with connect() as db:
            db.execute("SELECT 1").fetchone()
    except Exception:
        return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
    return {"status": "ready", "database": True, "model_runs": chat.capacity()}


@app.post("/api/chat/sessions")
def create_session():
    return chat.create_session()


@app.get("/api/chat/sessions")
def list_sessions(limit: int = Query(50, ge=1, le=100), scope: str = Query("active", pattern="^(active|archived)$")):
    return chat.list_sessions(limit, scope)


@app.get("/api/chat/sessions/{session_id}")
def get_session(session_id: str):
    session = chat.session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return {"session": session, "messages": chat.public_history(session_id)}


@app.patch("/api/chat/sessions/{session_id}")
def rename_session(session_id: str, payload: SessionTitleUpdate):
    session = chat.rename_session(session_id, payload.title)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@app.post("/api/chat/sessions/{session_id}/archive")
def archive_session(session_id: str):
    session = chat.archive_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@app.post("/api/chat/sessions/{session_id}/restore")
def restore_session(session_id: str):
    session = chat.restore_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@app.delete("/api/chat/sessions/{session_id}")
def delete_session(session_id: str):
    if not chat.delete_session(session_id):
        raise HTTPException(404, "会话不存在")
    return {"deleted": True, "recoverable": True}


@app.post("/api/chat/sessions/{session_id}/messages")
def send_message(session_id: str, payload: ChatMessageInput):
    session = chat.session(session_id, include_deleted=True)
    if session and session["deleted_at"]:
        raise HTTPException(404, "会话不存在")
    if session and session["archived_at"]:
        raise HTTPException(409, "已归档的会话不能继续发送消息，请先恢复")
    return StreamingResponse(
        chat.stream(session_id, payload.content),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.put("/api/chat/sessions/{session_id}/messages/{message_id}/feedback")
def update_message_feedback(session_id: str, message_id: str, payload: MessageFeedbackInput):
    result = chat.set_message_feedback(session_id, message_id, payload.rating)
    if not result:
        raise HTTPException(404, "可评价的客服回答不存在")
    return result


@app.post("/api/chat/sessions/{session_id}/runs/{run_id}/cancel")
def cancel_run(session_id: str, run_id: str):
    return {"cancelled": chat.cancel(session_id, run_id)}


@app.get("/api/admin/providers")
def list_providers():
    return gateway.list_configs()


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
):
    filters = {"lifecycle": scope or lifecycle, "q": q, "management_status": management_status, "assignee": assignee, "tag": tag, "provider": provider, "run_status": run_status, "has_contact": has_contact, "date_from": date_from, "date_to": date_to}
    return admin_service.list_sessions(filters, page, page_size)


@app.get("/api/admin/chat/sessions/{session_id}")
def admin_session_detail(session_id: str):
    detail = admin_service.session_detail(session_id)
    if not detail:
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.view", "session", session_id, "查看会话详情")
    return detail


@app.patch("/api/admin/chat/sessions/{session_id}")
def admin_update_session(session_id: str, payload: AdminSessionUpdate):
    detail = admin_service.update_session(session_id, payload.model_dump(exclude_unset=True))
    if not detail:
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.update", "session", session_id, "更新会话管理信息", {"fields": list(payload.model_fields_set)})
    return detail


@app.post("/api/admin/chat/sessions/{session_id}/notes")
def admin_add_session_note(session_id: str, payload: SessionNoteInput):
    note = admin_service.add_note(session_id, payload.content)
    if not note:
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.note", "session", session_id, "添加内部备注")
    return note


@app.get("/api/admin/chat/sessions/{session_id}/export")
def admin_export_session(session_id: str, format: str = Query("markdown", pattern="^(markdown|csv)$")):
    detail = admin_service.session_detail(session_id)
    if not detail:
        raise HTTPException(404, "会话不存在")
    content, media_type, filename = admin_service.export_session(detail, format)
    admin_service.audit("session.export", "session", session_id, f"导出会话 {format}")
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/admin/chat/sessions/{session_id}/archive")
def admin_archive_session(session_id: str):
    session = chat.archive_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.archive", "session", session_id, "归档会话")
    return session


@app.post("/api/admin/chat/sessions/{session_id}/restore")
def admin_restore_session(session_id: str):
    session = chat.restore_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.restore", "session", session_id, "恢复会话")
    return session


@app.delete("/api/admin/chat/sessions/{session_id}")
def admin_delete_session(session_id: str):
    if not chat.delete_session(session_id):
        raise HTTPException(404, "会话不存在")
    admin_service.audit("session.delete", "session", session_id, "安全删除会话")
    return {"deleted": True, "recoverable": True}


@app.get("/api/admin/overview")
def admin_overview():
    return admin_service.overview()


@app.get("/api/admin/audit")
def admin_audit(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), action: str | None = None):
    return admin_service.list_audit(page, page_size, action)


@app.get("/api/admin/prompts")
def admin_prompts():
    return prompt_service.list_prompts()


@app.post("/api/admin/prompts/drafts")
def admin_prompt_draft(payload: PromptDraftInput):
    draft = prompt_service.create_draft(payload.content, payload.change_note)
    admin_service.audit("prompt.draft", "prompt", draft["id"], f"创建提示词草稿 v{draft['version']}")
    return draft


@app.post("/api/admin/prompts/{prompt_id}/publish")
def admin_prompt_publish(prompt_id: str):
    published = prompt_service.publish(prompt_id)
    if not published:
        raise HTTPException(404, "提示词版本不存在")
    admin_service.audit("prompt.publish", "prompt", prompt_id, f"发布提示词 v{published['version']}")
    return published


@app.post("/api/admin/prompts/{prompt_id}/rollback")
def admin_prompt_rollback(prompt_id: str, payload: PromptRollbackInput):
    published = prompt_service.rollback(prompt_id, payload.change_note)
    if not published:
        raise HTTPException(404, "提示词版本不存在")
    admin_service.audit("prompt.rollback", "prompt", published["id"], f"回滚并发布为 v{published['version']}", {"source_id": prompt_id})
    return published


@app.put("/api/admin/providers/{provider}")
def update_provider(provider: str, payload: ProviderUpdate):
    try:
        gateway.update(provider, payload.model, payload.api_key)
        result = gateway.list_configs()
        admin_service.audit("provider.update", "provider", provider, "更新模型配置", {"model": payload.model, "key_changed": bool(payload.api_key)})
        return result
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.delete("/api/admin/providers/{provider}/key")
def delete_provider_key(provider: str):
    gateway.delete_key(provider)
    admin_service.audit("provider.key_delete", "provider", provider, "删除模型密钥")
    return gateway.list_configs()


@app.post("/api/admin/providers/{provider}/test")
async def test_provider(provider: str, payload: ProviderTest):
    try:
        return await gateway.test(provider, payload.model, payload.api_key)
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.put("/api/admin/active-provider")
def activate_provider(payload: ActiveProviderUpdate):
    try:
        gateway.activate(payload.provider)
        result = gateway.list_configs()
        admin_service.audit("provider.activate", "provider", payload.provider, "切换当前模型供应商")
        return result
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/api/admin/documents")
def list_documents():
    return knowledge.list_documents()


@app.get("/api/admin/knowledge/location")
def knowledge_location():
    return knowledge.location()


@app.post("/api/admin/knowledge/import")
def import_knowledge_path(payload: KnowledgePathInput):
    try:
        result = knowledge.import_path(payload.path, payload.recursive)
        admin_service.audit("knowledge.import", "knowledge", None, "从本机路径导入知识文档", {"imported_count": result["imported_count"]})
        return result
    except KnowledgeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/knowledge/jobs", status_code=202)
def create_knowledge_job(payload: KnowledgePathInput):
    try:
        job = knowledge_jobs.create(payload.path, payload.recursive)
        admin_service.audit("knowledge.job.create", "knowledge_job", job["id"], "创建知识库导入任务", {"source_path": job["source_path"]})
        return job
    except KnowledgeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/admin/knowledge/jobs")
def list_knowledge_jobs(limit: int = Query(20, ge=1, le=100)):
    return knowledge_jobs.list(limit)


@app.get("/api/admin/knowledge/jobs/{job_id}")
def get_knowledge_job(job_id: str):
    try:
        return knowledge_jobs.get(job_id)
    except KnowledgeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/documents")
async def upload_document(file: UploadFile = File(...)):
    try:
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        result = knowledge.ingest(file.filename or "document.txt", bytes(data))
        admin_service.audit("knowledge.upload", "document", result["id"], "上传知识文档", {"display_name": result["display_name"]})
        return result
    except KnowledgeDuplicateError as exc:
        raise HTTPException(409, str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "文档解析失败") from exc
    finally:
        await file.close()


@app.delete("/api/admin/documents/{document_id}")
def delete_document(document_id: str):
    try:
        knowledge.delete(document_id)
        admin_service.audit("knowledge.delete", "document", document_id, "删除知识文档")
        return {"deleted": True}
    except KnowledgeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/documents/{document_id}/reparse")
def reparse_document(document_id: str):
    try:
        result = knowledge.reparse(document_id)
        admin_service.audit("knowledge.reparse", "document", document_id, "重新解析知识文档", {"version": result["version"]})
        return result
    except KnowledgeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/index/rebuild")
def rebuild_index():
    result = {"indexed_chunks": knowledge.rebuild()}
    admin_service.audit("knowledge.rebuild", "knowledge", None, "重建知识库索引", result)
    return result


@app.get("/api/admin/documents/{document_id}")
def document_detail(document_id: str):
    with connect() as db:
        document = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not document:
            raise HTTPException(404, "文档不存在")
        chunks = db.execute("SELECT id,chunk_index,section,location,block_type,char_count,metadata_json,substr(content,1,1200) content FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index", (document_id,)).fetchall()
    document_value = dict(document)
    document_value["warnings"] = json.loads(document_value.pop("warnings_json", "[]") or "[]")
    document_value["metadata"] = json.loads(document_value.pop("metadata_json", "{}") or "{}")
    chunk_values = []
    for row in chunks:
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json", "{}") or "{}")
        chunk_values.append(value)
    return {"document": document_value, "chunks": chunk_values}


@app.post("/api/admin/knowledge/search-test")
def knowledge_search_test(payload: KnowledgeSearchInput):
    return {"items": knowledge.search(payload.query, payload.limit)}


@app.get("/api/admin/knowledge/terms")
def knowledge_terms():
    return {"defaults": DEFAULT_SYNONYM_GROUPS, "custom": custom_synonym_groups(), "all": all_synonym_groups()}


@app.put("/api/admin/knowledge/terms")
def update_knowledge_terms(payload: KnowledgeTermsUpdate):
    groups = save_custom_synonym_groups(payload.groups)
    admin_service.audit("knowledge.terms.update", "knowledge", None, "更新知识库同义词", {"group_count": len(groups)})
    return {"defaults": DEFAULT_SYNONYM_GROUPS, "custom": groups, "all": all_synonym_groups()}


@app.post("/api/contacts")
def create_contact(payload: ContactInput):
    contact_id = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO contacts(id,session_id,name,phone,email,summary,created_at) VALUES(?,?,?,?,?,?,?)",
            (contact_id, payload.session_id, payload.name, payload.phone, str(payload.email) if payload.email else None, payload.summary, utc_now()),
        )
    return {"id": contact_id, "saved": True}


@app.get("/api/admin/contacts")
def list_contacts(q: str | None = None, status: str | None = Query(None, pattern="^(new|following|closed)$"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    with connect() as db:
        clauses, params = ["1=1"], []
        if q:
            clauses.append("(name LIKE ? OR phone LIKE ? OR email LIKE ? OR summary LIKE ?)")
            params.extend([f"%{q}%"] * 4)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = " AND ".join(clauses)
        total = db.execute(f"SELECT count(*) FROM contacts WHERE {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT * FROM contacts WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.patch("/api/admin/contacts/{contact_id}")
def update_contact(contact_id: str, payload: ContactAdminUpdate):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400, "没有可更新字段")
    with connect() as db:
        result = db.execute(f"UPDATE contacts SET {','.join(f'{key}=?' for key in values)} WHERE id=?", [*values.values(), contact_id])
        if not result.rowcount:
            raise HTTPException(404, "联系记录不存在")
        row = db.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    admin_service.audit("contact.update", "contact", contact_id, "更新联系记录", {"fields": list(values)})
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
        target = FRONTEND_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
