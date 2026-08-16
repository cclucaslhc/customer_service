import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.chat import ChatOrchestrator
from app.database import connect, init_database, utc_now
from app.knowledge import KnowledgeError, KnowledgeService
from app.providers import ModelGateway, ProviderError
from app.schemas import ActiveProviderUpdate, ChatMessageInput, ContactInput, ProviderTest, ProviderUpdate
from app.security import SecretStore
from app.settings import FRONTEND_DIST, MASTER_KEY_PATH


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    yield


app = FastAPI(title="AI 客服", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
secrets = SecretStore()
gateway = ModelGateway(secrets)
knowledge = KnowledgeService()
chat = ChatOrchestrator(gateway, knowledge)


@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": True, "master_key": MASTER_KEY_PATH.exists()}


@app.post("/api/chat/sessions")
def create_session():
    return chat.create_session()


@app.get("/api/chat/sessions/{session_id}")
def get_session(session_id: str):
    with connect() as db:
        session = db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        raise HTTPException(404, "会话不存在")
    return {"session": dict(session), "messages": chat.history(session_id)}


@app.post("/api/chat/sessions/{session_id}/messages")
def send_message(session_id: str, payload: ChatMessageInput):
    return StreamingResponse(chat.stream(session_id, payload.content), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/sessions/{session_id}/runs/{run_id}/cancel")
def cancel_run(session_id: str, run_id: str):
    return {"cancelled": chat.cancel(session_id, run_id)}


@app.get("/api/admin/providers")
def list_providers():
    return gateway.list_configs()


@app.put("/api/admin/providers/{provider}")
def update_provider(provider: str, payload: ProviderUpdate):
    try:
        gateway.update(provider, payload.model, payload.api_key)
        return gateway.list_configs()
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.delete("/api/admin/providers/{provider}/key")
def delete_provider_key(provider: str):
    gateway.delete_key(provider)
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
        return gateway.list_configs()
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/api/admin/documents")
def list_documents():
    return knowledge.list_documents()


@app.post("/api/admin/documents")
async def upload_document(file: UploadFile = File(...)):
    try:
        data = await file.read()
        return knowledge.ingest(file.filename or "document.txt", data)
    except KnowledgeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, "文档解析失败") from exc


@app.delete("/api/admin/documents/{document_id}")
def delete_document(document_id: str):
    try:
        knowledge.delete(document_id)
        return {"deleted": True}
    except KnowledgeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/index/rebuild")
def rebuild_index():
    return {"indexed_chunks": knowledge.rebuild()}


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
def list_contacts():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM contacts ORDER BY created_at DESC").fetchall()]


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
