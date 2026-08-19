import csv
import io
import json
import uuid
from datetime import datetime

from app.chat import serialize_message
from app.database import connect, utc_now


def audit(action: str, entity_type: str, entity_id: str | None, summary: str, metadata: dict | None = None) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO audit_logs(id,action,entity_type,entity_id,summary,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), action, entity_type, entity_id, summary, json.dumps(metadata or {}, ensure_ascii=False), utc_now()),
        )


def _tags(value: str | None) -> list[str]:
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


def list_sessions(filters: dict, page: int, page_size: int) -> dict:
    clauses = ["1=1"]
    params: list = []
    lifecycle = filters.get("lifecycle", "all")
    lifecycle_sql = {
        "active": "s.deleted_at IS NULL AND s.archived_at IS NULL",
        "archived": "s.deleted_at IS NULL AND s.archived_at IS NOT NULL",
        "deleted": "s.deleted_at IS NOT NULL",
        "all": "1=1",
    }
    clauses.append(lifecycle_sql[lifecycle])
    for key, column in (("management_status", "s.management_status"), ("assignee", "s.assignee"), ("provider", "r.provider"), ("run_status", "r.status")):
        if filters.get(key):
            clauses.append(f"{column}=?")
            params.append(filters[key])
    if filters.get("q"):
        clauses.append("(s.title LIKE ? OR EXISTS(SELECT 1 FROM messages mq WHERE mq.session_id=s.id AND mq.content LIKE ?))")
        term = f"%{filters['q']}%"
        params.extend([term, term])
    if filters.get("tag"):
        clauses.append("s.tags_json LIKE ?")
        params.append(f'%"{filters["tag"]}"%')
    if filters.get("date_from"):
        clauses.append("s.updated_at>=?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("s.updated_at<=?")
        params.append(filters["date_to"])
    if filters.get("has_contact") is not None:
        clauses.append(("EXISTS" if filters["has_contact"] else "NOT EXISTS") + "(SELECT 1 FROM contacts c WHERE c.session_id=s.id)")
    where = " AND ".join(clauses)
    base = f"""
        FROM chat_sessions s
        LEFT JOIN agent_runs r ON r.id=(SELECT id FROM agent_runs latest WHERE latest.session_id=s.id ORDER BY latest.started_at DESC LIMIT 1)
        WHERE {where}
    """
    with connect() as db:
        total = db.execute(f"SELECT count(DISTINCT s.id) {base}", params).fetchone()[0]
        rows = db.execute(
            f"""SELECT s.*,r.provider,r.model,r.status last_run_status,r.error_code last_error_code,
                       (SELECT count(*) FROM messages m WHERE m.session_id=s.id AND m.status='complete') message_count,
                       (SELECT count(*) FROM agent_runs ar WHERE ar.session_id=s.id) run_count,
                       (SELECT count(*) FROM agent_runs ar WHERE ar.session_id=s.id AND ar.status='error') error_count,
                       (SELECT count(*) FROM contacts c WHERE c.session_id=s.id) contact_count
                {base} ORDER BY s.updated_at DESC LIMIT ? OFFSET ?""",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["tags"] = _tags(item.pop("tags_json", "[]"))
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def session_detail(session_id: str) -> dict | None:
    with connect() as db:
        session = db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            return None
        messages = db.execute("SELECT * FROM messages WHERE session_id=? AND status='complete' ORDER BY created_at,rowid", (session_id,)).fetchall()
        runs = db.execute(
            """SELECT r.*,p.version prompt_version FROM agent_runs r
               LEFT JOIN prompt_versions p ON p.id=r.prompt_version_id WHERE r.session_id=? ORDER BY r.started_at DESC""",
            (session_id,),
        ).fetchall()
        tools = db.execute("SELECT run_id,tool_name,status,started_at,finished_at FROM agent_run_tools WHERE run_id IN (SELECT id FROM agent_runs WHERE session_id=?) ORDER BY id", (session_id,)).fetchall()
        contacts = db.execute("SELECT * FROM contacts WHERE session_id=? ORDER BY created_at DESC", (session_id,)).fetchall()
        notes = db.execute("SELECT * FROM session_notes WHERE session_id=? ORDER BY created_at DESC", (session_id,)).fetchall()
        logs = db.execute("SELECT * FROM audit_logs WHERE entity_type='session' AND entity_id=? ORDER BY created_at DESC LIMIT 100", (session_id,)).fetchall()
    run_items = [dict(row) for row in runs]
    for run in run_items:
        run["tools"] = [dict(tool) for tool in tools if tool["run_id"] == run["id"]]
        if run.get("started_at") and run.get("finished_at"):
            try:
                run["duration_ms"] = int((datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds() * 1000)
            except ValueError:
                run["duration_ms"] = None
    item = dict(session)
    item["tags"] = _tags(item.pop("tags_json", "[]"))
    return {"session": item, "messages": [serialize_message(dict(row)) for row in messages], "runs": run_items, "contacts": [dict(row) for row in contacts], "notes": [dict(row) for row in notes], "audit": [dict(row) for row in logs]}


def update_session(session_id: str, values: dict) -> dict | None:
    allowed = {"title", "management_status", "assignee"}
    updates = {key: value for key, value in values.items() if key in allowed and value is not None}
    if "tags" in values and values["tags"] is not None:
        updates["tags_json"] = json.dumps(values["tags"], ensure_ascii=False)
    if not updates:
        return session_detail(session_id)
    updates["updated_at"] = utc_now()
    with connect() as db:
        result = db.execute(f"UPDATE chat_sessions SET {','.join(f'{key}=?' for key in updates)} WHERE id=?", [*updates.values(), session_id])
    return session_detail(session_id) if result.rowcount else None


def add_note(session_id: str, content: str) -> dict | None:
    with connect() as db:
        if not db.execute("SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)).fetchone():
            return None
        note_id, now = str(uuid.uuid4()), utc_now()
        db.execute("INSERT INTO session_notes(id,session_id,content,created_at) VALUES(?,?,?,?)", (note_id, session_id, content.strip(), now))
    return {"id": note_id, "session_id": session_id, "content": content.strip(), "created_at": now}


def overview() -> dict:
    with connect() as db:
        session_count = db.execute("SELECT count(*) FROM chat_sessions WHERE deleted_at IS NULL").fetchone()[0]
        open_count = db.execute("SELECT count(*) FROM chat_sessions WHERE deleted_at IS NULL AND management_status!='resolved'").fetchone()[0]
        run_count = db.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
        complete = db.execute("SELECT count(*) FROM agent_runs WHERE status='complete'").fetchone()[0]
        errors = db.execute("SELECT count(*) FROM agent_runs WHERE status='error'").fetchone()[0]
        contacts = db.execute("SELECT count(*) FROM contacts").fetchone()[0]
        documents = db.execute("SELECT count(*) FROM documents WHERE status='ready'").fetchone()[0]
        chunks = db.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
        recent = [dict(row) for row in db.execute("SELECT id,session_id,error_code,provider,model,started_at FROM agent_runs WHERE status='error' ORDER BY started_at DESC LIMIT 8").fetchall()]
        providers = [dict(row) for row in db.execute("SELECT provider,model,encrypted_key IS NOT NULL configured,last_test_status,last_test_at FROM provider_configs ORDER BY provider").fetchall()]
        active_provider = db.execute("SELECT value FROM app_settings WHERE key='active_provider'").fetchone()[0]
    return {"sessions": session_count, "unresolved": open_count, "runs": run_count, "completion_rate": round(complete / run_count * 100, 1) if run_count else 0, "error_rate": round(errors / run_count * 100, 1) if run_count else 0, "contacts": contacts, "documents": documents, "chunks": chunks, "active_provider": active_provider, "providers": providers, "recent_errors": recent}


def list_audit(page: int, page_size: int, action: str | None = None) -> dict:
    where, params = ("WHERE action LIKE ?", [f"%{action}%"]) if action else ("", [])
    with connect() as db:
        total = db.execute(f"SELECT count(*) FROM audit_logs {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT * FROM audit_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


def export_session(detail: dict, export_format: str) -> tuple[str, str, str]:
    session = detail["session"]
    if export_format == "markdown":
        lines = [f"# {session['title']}", "", f"- 会话 ID：{session['id']}", f"- 管理状态：{session['management_status']}", "", "## 对话", ""]
        for message in detail["messages"]:
            lines.extend([f"### {'客户' if message['role'] == 'user' else '客服'} · {message['created_at']}", "", message["content"], ""])
        return "\n".join(lines), "text/markdown; charset=utf-8", f"session-{session['id']}.md"
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["role", "created_at", "content"])
    for message in detail["messages"]:
        content = message["content"]
        if content.startswith(("=", "+", "-", "@")):
            content = "'" + content
        writer.writerow([message["role"], message["created_at"], content])
    return "\ufeff" + output.getvalue(), "text/csv; charset=utf-8", f"session-{session['id']}.csv"
