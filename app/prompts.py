import hashlib
import uuid

from app.database import connect, utc_now
from app.prompting import DEFAULT_BUSINESS_PROMPT, SAFETY_BASELINE, combined_instructions


def _serialize(row) -> dict | None:
    return dict(row) if row else None


def active_prompt() -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM prompt_versions WHERE status='published' ORDER BY version DESC LIMIT 1").fetchone()
    if not row:
        return {"id": None, "version": 0, "content": DEFAULT_BUSINESS_PROMPT, "status": "legacy"}
    return dict(row)


def runtime_prompt() -> tuple[str | None, str]:
    current = active_prompt()
    return current.get("id"), combined_instructions(current["content"])


def list_prompts() -> dict:
    with connect() as db:
        rows = db.execute("SELECT * FROM prompt_versions ORDER BY version DESC").fetchall()
    versions = [dict(row) for row in rows]
    return {"baseline": SAFETY_BASELINE, "current": next((item for item in versions if item["status"] == "published"), None), "versions": versions}


def create_draft(content: str, change_note: str) -> dict:
    content = content.strip()
    now = utc_now()
    prompt_id = str(uuid.uuid4())
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with connect() as db:
        version = db.execute("SELECT coalesce(max(version),0)+1 FROM prompt_versions").fetchone()[0]
        db.execute(
            "INSERT INTO prompt_versions(id,version,content,status,change_note,checksum,created_at) VALUES(?,?,?,?,?,?,?)",
            (prompt_id, version, content, "draft", change_note.strip(), checksum, now),
        )
        row = db.execute("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,)).fetchone()
    return dict(row)


def publish(prompt_id: str) -> dict | None:
    now = utc_now()
    with connect() as db:
        target = db.execute("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,)).fetchone()
        if not target:
            return None
        db.execute("UPDATE prompt_versions SET status='archived' WHERE status='published'")
        db.execute("UPDATE prompt_versions SET status='published',published_at=? WHERE id=?", (now, prompt_id))
        return dict(db.execute("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,)).fetchone())


def rollback(source_id: str, change_note: str) -> dict | None:
    with connect() as db:
        source = db.execute("SELECT content,version FROM prompt_versions WHERE id=?", (source_id,)).fetchone()
    if not source:
        return None
    draft = create_draft(source["content"], change_note.strip() or f"回滚到 v{source['version']}")
    return publish(draft["id"])
