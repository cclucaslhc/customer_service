import csv
import io
import json
from datetime import datetime

from app.chat import serialize_message
from app.database import utc_now
from app.persistence.admin_repository import LocalAdminRepository


def _tags(value) -> list[str]:
    try:
        result = json.loads(value or "[]") if isinstance(value, str) else value
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


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


class AdminService:
    def __init__(self, repository=None):
        self.repository = repository or LocalAdminRepository()

    def audit(self, action: str, entity_type: str, entity_id: str | None, summary: str, metadata: dict | None = None) -> None:
        self.repository.audit(action=action, entity_type=entity_type, entity_id=entity_id, summary=summary, metadata=metadata or {}, now=utc_now())

    def list_sessions(self, filters: dict, page: int, page_size: int) -> dict:
        result = self.repository.managed_sessions(filters, page, page_size)
        for item in result["items"]:
            item["tags"] = _tags(item.pop("tags_json", "[]"))
        return result

    def session_detail(self, session_id: str) -> dict | None:
        detail = self.repository.detail(session_id)
        if not detail:
            return None
        detail["session"]["tags"] = _tags(detail["session"].pop("tags_json", "[]"))
        detail["messages"] = [serialize_message(row) for row in detail["messages"]]
        for run in detail["runs"]:
            if run.get("started_at") and run.get("finished_at"):
                try:
                    run["duration_ms"] = int((datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds() * 1000)
                except ValueError:
                    run["duration_ms"] = None
        return detail

    def update_session(self, session_id: str, values: dict) -> dict | None:
        updates = {key: value for key, value in values.items() if key in {"title", "management_status", "assignee"} and value is not None}
        if values.get("tags") is not None:
            updates["tags_json"] = values["tags"]
        if updates and not self.repository.update_session(session_id, now=utc_now(), include_deleted=True, **updates):
            return None
        return self.session_detail(session_id)

    def add_note(self, session_id: str, content: str) -> dict | None:
        return self.repository.add_note(session_id, content, utc_now())

    def overview(self) -> dict:
        return self.repository.overview()

    def list_audit(self, page: int, page_size: int, action: str | None = None) -> dict:
        return self.repository.list_audit(page, page_size, action)

    export_session = staticmethod(export_session)


repository = LocalAdminRepository()


def audit(action: str, entity_type: str, entity_id: str | None, summary: str, metadata: dict | None = None) -> None:
    return AdminService(repository).audit(action, entity_type, entity_id, summary, metadata)


def list_sessions(filters: dict, page: int, page_size: int) -> dict:
    return AdminService(repository).list_sessions(filters, page, page_size)


def session_detail(session_id: str) -> dict | None:
    return AdminService(repository).session_detail(session_id)


def update_session(session_id: str, values: dict) -> dict | None:
    return AdminService(repository).update_session(session_id, values)


def add_note(session_id: str, content: str) -> dict | None:
    return AdminService(repository).add_note(session_id, content)


def overview() -> dict:
    return AdminService(repository).overview()


def list_audit(page: int, page_size: int, action: str | None = None) -> dict:
    return AdminService(repository).list_audit(page, page_size, action)
