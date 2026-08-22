from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.database import connect, utc_now
from app.knowledge import KnowledgeError, KnowledgeService


class KnowledgeJobService:
    """Persistent, single-worker knowledge imports for predictable local resource use."""

    def __init__(self, knowledge: KnowledgeService) -> None:
        self.knowledge = knowledge
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowledge-import")
        self._lock = threading.Lock()
        self._submitted: set[str] = set()

    def create(self, source_path: str, recursive: bool = True) -> dict:
        # Validate before returning a task id so obvious path/security errors stay synchronous.
        source, candidates = self.knowledge._import_candidates(source_path, recursive)
        job_id = str(uuid.uuid4())
        now = utc_now()
        with connect() as db:
            db.execute(
                """INSERT INTO knowledge_jobs(
                       id,source_path,recursive,status,stage,total_count,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (job_id, str(source), int(recursive), "pending", "pending", len(candidates), now),
            )
        self._submit(job_id)
        return self.get(job_id)

    def recover(self) -> None:
        with connect() as db:
            rows = db.execute(
                "SELECT id FROM knowledge_jobs WHERE status IN ('pending','running') ORDER BY created_at"
            ).fetchall()
            db.execute(
                "UPDATE knowledge_jobs SET status='pending',stage='pending',current_file=NULL,error_message=NULL WHERE status='running'"
            )
        for row in rows:
            self._submit(row["id"])

    def _submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._submitted:
                return
            self._submitted.add(job_id)
        self._executor.submit(self._run, job_id)

    def _update(self, job_id: str, values: dict) -> None:
        allowed = {
            "status", "stage", "total_count", "processed_count", "imported_count",
            "skipped_count", "failed_count", "current_file", "result_json",
            "error_message", "started_at", "finished_at",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        assignments = ",".join(f"{key}=?" for key in values)
        with connect() as db:
            db.execute(
                f"UPDATE knowledge_jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

    def _run(self, job_id: str) -> None:
        try:
            with connect() as db:
                row = db.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return
            self._update(job_id, {"status": "running", "stage": "scanning", "started_at": utc_now()})

            def progress(event: dict) -> None:
                self._update(job_id, event)

            result = self.knowledge.import_path(row["source_path"], bool(row["recursive"]), progress)
            if result["failed_count"] and not result["imported_count"] and not result["skipped_count"]:
                status = "failed"
            elif result["failed_count"]:
                status = "partial"
            else:
                status = "complete"
            self._update(job_id, {
                "status": status,
                "stage": status,
                "processed_count": result["imported_count"] + result["skipped_count"] + result["failed_count"],
                "imported_count": result["imported_count"],
                "skipped_count": result["skipped_count"],
                "failed_count": result["failed_count"],
                "current_file": None,
                "result_json": json.dumps(result, ensure_ascii=False),
                "finished_at": utc_now(),
            })
        except KnowledgeError as exc:
            self._update(job_id, {"status": "failed", "stage": "failed", "error_message": str(exc), "finished_at": utc_now()})
        except Exception:
            self._update(job_id, {"status": "failed", "stage": "failed", "error_message": "导入任务执行失败", "finished_at": utc_now()})
        finally:
            with self._lock:
                self._submitted.discard(job_id)

    def get(self, job_id: str) -> dict:
        with connect() as db:
            row = db.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KnowledgeError("导入任务不存在")
        return self._serialize(dict(row))

    def list(self, limit: int = 20) -> list[dict]:
        with connect() as db:
            rows = db.execute("SELECT * FROM knowledge_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._serialize(dict(row)) for row in rows]

    @staticmethod
    def _serialize(row: dict) -> dict:
        row["recursive"] = bool(row["recursive"])
        row["result"] = json.loads(row.pop("result_json", "{}") or "{}")
        return row
