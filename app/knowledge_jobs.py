from __future__ import annotations

import json
import threading
import uuid
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from app.database import utc_now
from app.knowledge import KnowledgeError, KnowledgeService, is_within, normalize_filename, decode_json
from app.settings import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES


class KnowledgeJobService:
    """Persistent, single-worker knowledge imports for predictable local resource use."""

    def __init__(self, knowledge: KnowledgeService, *, allow_local_worker=True, durable=False) -> None:
        self.knowledge = knowledge
        self.repository = knowledge.repository
        self.allow_local_worker = allow_local_worker
        self.durable = durable
        self._executor = None
        self._lock = threading.Lock()
        self._submitted: set[str] = set()

    def create(self, source_path: str, recursive: bool = True) -> dict:
        if self.durable:
            if self.knowledge._namespace is None:
                raise KnowledgeError('持久导入需要显式 PostgreSQL 租户身份')
            source,candidates = self.knowledge._import_candidates(source_path,recursive)
            from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
            queue = DurableKnowledgeRepository(self.repository.engine,token=self.repository._token)
            manifest,total_bytes = [],0
            for index,candidate in enumerate(candidates):
                path = candidate.resolve()
                if not is_within(path,self.knowledge.knowledge_dir):
                    raise KnowledgeError('导入文件必须位于租户托管目录')
                size = path.stat().st_size
                supported = path.suffix.lower() in ALLOWED_EXTENSIONS and size <= MAX_UPLOAD_BYTES
                digest = None
                input_object = None
                if supported:
                    total_bytes += size
                    if total_bytes > 100*1024*1024:
                        raise KnowledgeError('单次后台导入最多 100 MiB')
                    # Bounded read; a concurrently growing file cannot allocate
                    # unbounded memory or silently change the queued manifest.
                    with path.open('rb') as file:
                        data = file.read(MAX_UPLOAD_BYTES+1)
                    if len(data) != size:
                        raise KnowledgeError('扫描期间文件发生变化，请重试')
                    digest = hashlib.sha256(data).hexdigest()
                    if self.knowledge.object_store:
                        self.repository.require_manage()
                        input_object = self.knowledge.object_store.put(self.knowledge._namespace, data, kind='knowledge').to_dict()
                manifest.append({'position':index,'relative_path':str(path.relative_to(self.knowledge.knowledge_dir.resolve())).replace('\\','/'),
                    'sha256':digest,'size':size,'input_object':input_object})
            job_id = str(uuid.uuid4())
            from app.tracing import capture_carrier
            queue.enqueue(job_id,str(source),bool(recursive),manifest,trace_context=capture_carrier())
            return self.get(job_id)
        if not self.allow_local_worker:
            raise KnowledgeError("PostgreSQL 导入任务需要租约 Worker，当前尚未启用")
        # Validate before returning a task id so obvious path/security errors stay synchronous.
        source, candidates = self.knowledge._import_candidates(source_path, recursive)
        job_id = str(uuid.uuid4())
        now = utc_now()
        self.repository.create_job({"id": job_id, "source_path": str(source), "recursive": bool(recursive),
                                    "status": "pending", "stage": "pending", "total_count": len(candidates), "created_at": now})
        self._submit(job_id)
        return self.get(job_id)

    def _enqueue_content(self, filename, data, *, replacement=None):
        if not self.durable or self.knowledge._namespace is None:
            raise KnowledgeError('后台文档处理需要已认证的持久队列')
        self.repository.require_manage()
        display_name = normalize_filename(filename.replace('\\','/'))
        extension = Path(display_name).suffix.lower()
        if len(display_name) > 255 or extension not in ALLOWED_EXTENSIONS:
            raise KnowledgeError('文件名或格式不受支持')
        if not isinstance(data,bytes) or not data or len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeError('文件为空或超过单文件大小限制')
        job_id = str(uuid.uuid4())
        relative = f'jobs/{job_id}/input{extension}'
        reference = None
        if self.knowledge.object_store:
            reference = self.knowledge.object_store.put(self.knowledge._namespace,data,kind='knowledge').to_dict()
        else:
            target = (self.knowledge.knowledge_dir/relative).resolve()
            if not is_within(target,self.knowledge.knowledge_dir):
                raise KnowledgeError('文档暂存路径无效')
            target.parent.mkdir(parents=True,mode=0o700,exist_ok=False)
            with target.open('xb') as stream:
                stream.write(data)
        item = dict(position=0,relative_path=relative,display_name=display_name,
            size=len(data),sha256=hashlib.sha256(data).hexdigest(),input_object=reference,
            replace_document_id=replacement[0] if replacement else None,
            replace_document_version=replacement[1] if replacement else None)
        from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
        from app.tracing import capture_carrier
        queue = DurableKnowledgeRepository(self.repository.engine,token=self.repository._token)
        # Keep staging on uncertain commit acknowledgements; manifest-aware GC
        # must decide whether an object/file is still referenced.
        queue.enqueue(job_id,f'reparse://{replacement[0]}' if replacement else f'upload://{job_id}',
            False,[item],trace_context=capture_carrier())
        return self.get(job_id)

    def create_upload(self, filename, data):
        return self._enqueue_content(filename,data)

    def create_reparse(self, document_id):
        self.repository.require_manage()
        try:
            document_id = str(uuid.UUID(str(document_id)))
        except ValueError:
            raise KnowledgeError('文档不存在') from None
        row = self.repository.document(document_id)
        if not row:
            raise KnowledgeError('文档不存在')
        if decode_json(row.get('metadata_json'),{}).get('_private_object') is not None:
            data = self.knowledge._object_content(row,kind='knowledge')
        else:
            source = (self.knowledge.knowledge_dir/row['stored_name']).resolve()
            if not is_within(source,self.knowledge.knowledge_dir) or not source.is_file():
                raise KnowledgeError('原文不存在，无法重新解析')
            with source.open('rb') as stream:
                data = stream.read(MAX_UPLOAD_BYTES+1)
        if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise KnowledgeError('已发布原文发生变化，请重新上传')
        return self._enqueue_content(row['display_name'],data,replacement=(row['id'],row['version']))

    def recover(self) -> None:
        for job_id in self.repository.recover_local_jobs():
            self._submit(job_id)

    def _submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._submitted:
                return
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowledge-import")
            self._submitted.add(job_id)
        self._executor.submit(self._run, job_id)

    def close(self):
        with self._lock:
            executor, self._executor = self._executor, None
        if executor:
            executor.shutdown(wait=True)

    def _update(self, job_id: str, values: dict) -> None:
        allowed = {
            "status", "stage", "total_count", "processed_count", "imported_count",
            "skipped_count", "failed_count", "current_file", "result_json",
            "error_message", "started_at", "finished_at",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        self.repository.update_job(job_id, values)

    def _run(self, job_id: str) -> None:
        try:
            row = self.repository.job(job_id)
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
                "result_json": result,
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
        row = self.repository.job(job_id)
        if not row:
            raise KnowledgeError("导入任务不存在")
        return self._serialize(dict(row))

    def cancel(self, job_id: str) -> dict:
        if not self.durable:
            raise KnowledgeError('当前模式不支持取消后台任务')
        from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
        try:
            job_id = str(uuid.UUID(str(job_id)))
            DurableKnowledgeRepository(self.repository.engine,token=self.repository._token).cancel(job_id)
        except ValueError as exc:
            raise KnowledgeError('导入任务不存在') from exc
        return self.get(job_id)

    def list(self, limit: int = 20) -> list[dict]:
        rows = self.repository.jobs(limit)
        return [self._serialize(dict(row)) for row in rows]

    @staticmethod
    def _serialize(row: dict) -> dict:
        row['cancellation_supported'] = row.get('execution_mode') == 'queued'
        for key in ('execution_mode','lease_owner','lease_until','lease_fence','execution_slot_held'):
            row.pop(key,None)
        row["recursive"] = bool(row["recursive"])
        value = row.pop("result_json", {})
        row["result"] = json.loads(value or "{}") if isinstance(value, str) else value
        return row
