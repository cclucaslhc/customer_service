import hashlib
import json
import logging
import math
import re
import shutil
import unicodedata
import uuid
from sqlalchemy.exc import SQLAlchemyError
from pathlib import Path
from typing import Callable

import jieba

from app.database import utc_now
from app.object_store import ObjectReference, ObjectStoreError
from app.persistence.knowledge_repository import KnowledgeRepository, KnowledgeWriteConflict, LocalKnowledgeRepository
from app.knowledge_chunking import chunk_document, chunks_as_dicts
from app.knowledge_models import CHUNKER_VERSION, PARSER_VERSION, ContentBlock, DocumentParseError, ParsedDocument
from app.knowledge_parsers import parse_document as parse_structured_document
from app.knowledge_terms import STOPWORDS, expand_query
from app.knowledge_retrieval import (KnowledgeIndexChanged, RagConfigurationError, RagModelUnavailable,
                                     RepositoryLexicalRetriever, RepositoryVectorRetriever)
from app.rag_fusion import RankedCandidate, reciprocal_rank_fusion
from app.rag_model_manifest import EMBEDDING_WEIGHT, RERANKER_WEIGHT
from app.tracing import operation_scope
from app.identity import AuthenticationError,AuthorizationError
from app.local_inference_usage import LocalInferenceAccountingError
from app.settings import ALLOWED_EXTENSIONS, KNOWLEDGE_ASSET_DIR, KNOWLEDGE_DIR, LEGACY_UPLOAD_DIR, MAX_UPLOAD_BYTES, ROOT, SECRETS_DIR


def decode_json(value, default):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if isinstance(value, type(default)) else default


class KnowledgeError(ValueError):
    pass


class KnowledgeDuplicateError(KnowledgeError):
    pass


TOKENIZER_VERSION = "2.0"
logger = logging.getLogger(__name__)


def is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def normalize_filename(filename: str) -> str:
    value = Path(filename).name
    try:
        repaired = value.encode("latin-1").decode("utf-8")
        if repaired != value and re.search(r"[\u3400-\u9fff]", repaired):
            value = repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    value = unicodedata.normalize("NFC", value).strip()
    if not value or value in {".", ".."}:
        raise KnowledgeError("文件名无效")
    return value


def _word_terms(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    terms = [
        term.strip() for term in jieba.cut_for_search(normalized)
        if term.strip() and term.strip() not in STOPWORDS and re.search(r"[\w\u3400-\u9fff]", term)
    ]
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9._-]*", normalized)
    return list(dict.fromkeys([*terms, *ascii_terms]))


def _character_ngrams(text: str, size: int = 2) -> list[str]:
    compact = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", text.casefold())
    if len(compact) < size:
        return [compact] if compact else []
    return [compact[index : index + size] for index in range(len(compact) - size + 1)]


def tokenize(text: str) -> str:
    ngrams = [f"cn_{value}" for value in _character_ngrams(text)]
    return " ".join(dict.fromkeys([*_word_terms(text), *ngrams]))


def parse_document(data: bytes, extension: str) -> list[tuple[str, str]]:
    try:
        parsed = parse_structured_document(f"document{extension}", data)
    except DocumentParseError as exc:
        raise KnowledgeError(str(exc)) from exc
    sections: list[tuple[str, str]] = []
    for block in parsed.blocks:
        text = block.normalized_text()
        if text:
            sections.append((block.title or block.location or "正文", text))
    return sections


def chunk_sections(sections: list[tuple[str, str]], target_chars: int = 900) -> list[dict]:
    document = ParsedDocument(
        "legacy-sections.txt",
        ".txt",
        [ContentBlock("paragraph", text, location, location) for location, text in sections],
    )
    return chunks_as_dicts(chunk_document(document, target_chars=target_chars))


class KnowledgeService:
    def __init__(self, *, repository=None, knowledge_dir=None, asset_dir=None, legacy_upload_dir=None,
                 object_store=None, lexical_retriever=None, vector_retriever=None,
                 embedding_gateway=None, reranker=None, document_parser=None):
        self.repository = repository or LocalKnowledgeRepository()
        self.lexical_retriever = lexical_retriever or RepositoryLexicalRetriever(self.repository)
        if vector_retriever is not None and embedding_gateway is not None:
            raise ValueError('Select one vector retrieval source')
        if isinstance(self.repository,KnowledgeRepository) and self.repository.engine.dialect.name=='postgresql':
            from app.rag_local import LocalEmbeddingGateway,LocalReranker
            from app.local_inference_usage import AuditInferenceMeter
            if isinstance(embedding_gateway,LocalEmbeddingGateway):
                embedding_gateway=embedding_gateway.with_meter(AuditInferenceMeter.for_knowledge(self.repository,EMBEDDING_WEIGHT))
            if isinstance(reranker,LocalReranker):
                reranker=reranker.with_meter(AuditInferenceMeter.for_knowledge(self.repository,RERANKER_WEIGHT))
        self.vector_retriever = vector_retriever or (RepositoryVectorRetriever(self.repository, embedding_gateway,
            lexical_version=TOKENIZER_VERSION, chunker_version=CHUNKER_VERSION,
            reranker_version=RERANKER_WEIGHT.revision if reranker is not None else None)
                                                     if embedding_gateway is not None else None)
        self.reranker = reranker
        self.document_parser = document_parser
        self.last_degraded_reason = None
        self.object_store = object_store
        self._knowledge_dir = knowledge_dir
        self._asset_dir = asset_dir
        self._legacy_upload_dir = legacy_upload_dir
        # Raw/local construction remains lazy and must not inspect the default DB.
        self._namespace = self.repository.storage_namespace() if isinstance(self.repository, KnowledgeRepository) else None
        if self._namespace:
            self._knowledge_dir = Path(knowledge_dir or KNOWLEDGE_DIR) / self._namespace
            self._asset_dir = Path(asset_dir or KNOWLEDGE_ASSET_DIR) / self._namespace
            self._legacy_upload_dir = Path(legacy_upload_dir or LEGACY_UPLOAD_DIR) / self._namespace

    @property
    def knowledge_dir(self):
        return self._knowledge_dir or KNOWLEDGE_DIR

    @property
    def asset_dir(self):
        return self._asset_dir or KNOWLEDGE_ASSET_DIR

    @property
    def legacy_upload_dir(self):
        return self._legacy_upload_dir or LEGACY_UPLOAD_DIR

    def _asset_version_dir(self, document_id: str, version: int) -> Path:
        directory = (self.asset_dir / document_id / f"v{version}").resolve()
        if not is_within(directory, self.asset_dir):
            raise KnowledgeError("图片资产存储路径无效")
        return directory

    def _store_assets(self, parsed: ParsedDocument, document_id: str, version: int) -> list[dict]:
        if not parsed.assets:
            return []
        directory = self._asset_version_dir(document_id, version).with_name(f"v{version}-{uuid.uuid4().hex}")
        directory.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        stored: list[dict] = []
        try:
            for asset in parsed.assets:
                asset_id = str(uuid.uuid4())
                filename = f"{asset_id}{asset.extension}"
                path = directory / filename
                metadata = dict(asset.metadata)
                metadata.pop('_private_object', None)
                if self.object_store:
                    self.repository.require_manage()
                    metadata['_private_object'] = self.object_store.put(self._namespace, asset.data, kind='assets').to_dict()
                else:
                    path.write_bytes(asset.data)
                stored.append({
                    "id": asset_id,
                    "document_id": document_id,
                    "asset_index": asset.asset_index,
                    "stored_name": str(path.relative_to(self.asset_dir)).replace("\\", "/"),
                    "media_type": asset.media_type,
                    "size": len(asset.data),
                    "width": asset.width,
                    "height": asset.height,
                    "location": asset.location,
                    "alt_text": asset.alt_text,
                    "sha256": hashlib.sha256(asset.data).hexdigest(),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False),
                    "created_at": now,
                })
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return stored

    def _cleanup_asset_versions(self, document_id: str, keep_version: int | None = None) -> None:
        root = (self.asset_dir / document_id).resolve()
        if not is_within(root, self.asset_dir) or not root.exists():
            return
        for child in root.iterdir():
            if keep_version is not None and child.name == f"v{keep_version}":
                continue
            if child.is_dir() and is_within(child, root):
                shutil.rmtree(child)
        if keep_version is None and root.exists() and not any(root.iterdir()):
            root.rmdir()

    def _cleanup_asset_records(self, records: list[dict]) -> None:
        """Remove only exact previous/failed manifest files, never another attempt."""
        for record in records:
            path = (self.asset_dir / record["stored_name"]).resolve()
            if not is_within(path, self.asset_dir):
                continue
            path.unlink(missing_ok=True)
            if path.parent != self.asset_dir.resolve() and path.parent.is_dir() and not any(path.parent.iterdir()):
                path.parent.rmdir()

    def _managed_path(self, filename: str, digest: str, source_path: Path | None = None) -> Path:
        if source_path:
            resolved_source = source_path.resolve()
            try:
                resolved_source.relative_to(self.knowledge_dir.resolve())
                return resolved_source
            except ValueError:
                pass

        # Independent attempts must not overwrite/unlink each other's files.
        return self.knowledge_dir / f"{uuid.uuid4().hex}-{digest[:12]}{Path(filename).suffix.lower()}"

    def ingest(
        self,
        filename: str,
        data: bytes,
        source_path: Path | None = None,
        force: bool = False,
        progress: Callable[[dict], None] | None = None,
        immutable_copy: bool = False,
        _replace_document: tuple[str, int] | None = None,
        _source_identity: str | None = None,
    ) -> dict:
        self.repository.require_manage()
        if self.object_store and not self._namespace:
            raise KnowledgeError('对象存储要求已认证的租户知识库')
        if self._namespace and source_path and not is_within(source_path, self.knowledge_dir):
            raise KnowledgeError("不能从租户托管目录外导入文件")
        display_name = normalize_filename(filename)
        extension = Path(display_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise KnowledgeError(f"不支持扩展名 {extension or '（无扩展名）'}")
        if len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeError(f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if not data:
            raise KnowledgeError("文件为空")
        digest = hashlib.sha256(data).hexdigest()
        source_value = _source_identity or (str(source_path.resolve()) if source_path else None)
        existing, duplicate = self.repository.lookup_ingest(source_value, digest)
        if _replace_document:
            existing = self.repository.document(_replace_document[0])
            if not existing or existing['version'] != _replace_document[1]:
                raise KnowledgeError('文档在重新解析期间已变化')
            source_value = existing['source_path']
        if duplicate and (existing is None or duplicate["id"] != existing["id"]):
            raise KnowledgeDuplicateError("该文件已经建立索引")
        if existing is not None and existing["sha256"] == digest and not force:
            raise KnowledgeDuplicateError("该文件内容未变化，已跳过")
        doc_id = existing["id"] if existing else str(uuid.uuid4())
        version = int(existing["version"] or 1) + 1 if existing else 1
        previous_managed_path = (self.knowledge_dir / existing["stored_name"]).resolve() if existing else None
        previous_assets = self.repository.assets([doc_id]) if existing else []
        stored_assets = []
        owned_copy = False
        published = False
        try:
            if progress:
                progress({"stage": "parsing", "current_file": display_name})
            try:
                parsed = (self.document_parser or parse_structured_document)(display_name, data)
            except DocumentParseError as exc:
                raise KnowledgeError(str(exc)) from exc
            if progress:
                progress({"stage": "chunking", "current_file": display_name})
            chunks = chunks_as_dicts(chunk_document(parsed))
            if not chunks:
                raise KnowledgeError("文档没有可索引的文字")
            path = self._managed_path(display_name, digest, None if immutable_copy else source_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self.object_store and (source_path is None or path.resolve() != source_path.resolve()):
                with path.open("xb") as destination:
                    owned_copy = True
                    destination.write(data)
            stored_name = str(path.relative_to(self.knowledge_dir))
            source_value = source_value or str(path.resolve())
            source_mtime = source_path.stat().st_mtime if source_path and source_path.exists() else (None if self.object_store else path.stat().st_mtime)
            now = utc_now()
            stored_assets = self._store_assets(parsed, doc_id, version)
            parsed.metadata["asset_count"] = len(stored_assets)
            parsed.metadata.pop('_private_object', None)
            if self.object_store:
                self.repository.require_manage()
                parsed.metadata['_private_object'] = self.object_store.put(self._namespace, data, kind='knowledge').to_dict()
            if progress:
                progress({"stage": "indexing", "current_file": display_name})
            document = {
                "id": doc_id, "display_name": display_name, "stored_name": stored_name, "extension": extension,
                "size": len(data), "sha256": digest, "status": "ready", "error_message": None, "chunk_count": len(chunks),
                "detected_type": parsed.document_type, "source_path": source_value, "source_mtime": source_mtime,
                "parser_version": PARSER_VERSION, "chunker_version": CHUNKER_VERSION, "indexed_at": now,
                "version": version, "warnings_json": parsed.warnings, "metadata_json": parsed.metadata, "created_at": now,
            }
            chunk_rows = [
                {"document_id": doc_id, "chunk_index": index, "section": chunk["section"], "location": chunk["location"],
                 "content": chunk["content"], "tokenized": tokenize(chunk["content"]), "block_type": chunk["block_type"],
                 "metadata_json": chunk["metadata"], "char_count": len(chunk["content"])}
                for index, chunk in enumerate(chunks)
            ]
            try:
                self.repository.publish_document(document, chunk_rows, stored_assets, existing["version"] if existing else None)
            except KnowledgeWriteConflict as exc:
                raise KnowledgeError("文档在解析期间已被更新或导入，请刷新后重试") from exc
            published = True
            try:
                if (previous_managed_path and previous_managed_path != path.resolve()
                        and (source_path is None or previous_managed_path != source_path.resolve())
                        and is_within(previous_managed_path, self.knowledge_dir)):
                    previous_managed_path.unlink(missing_ok=True)
                self._cleanup_asset_records(previous_assets)
            except OSError as exc:
                # Cleanup failure cannot undo committed DB metadata or remove
                # the newly published files. Orphan GC is a separate S1 task.
                logger.warning("knowledge cleanup deferred document_id=%s exception_type=%s", doc_id, type(exc).__name__)
        except Exception as exc:
            # A transport error may be a lost COMMIT acknowledgement. Deleting
            # staging files would then corrupt an already committed document.
            # Keep ambiguous files for later manifest-aware reconciliation/GC.
            if not published and not isinstance(exc,SQLAlchemyError):
                cleanup_path = locals().get("path")
                if owned_copy and cleanup_path:
                    cleanup_path.unlink(missing_ok=True)
                self._cleanup_asset_records(stored_assets)
            raise
        return {
            "id": doc_id, "display_name": display_name, "status": "ready",
            "chunk_count": len(chunks), "detected_type": parsed.document_type,
            "version": version, "asset_count": len(stored_assets),
            "warnings": parsed.warnings,
        }

    def location(self) -> dict:
        self.repository.require_manage()
        return {
            "path": str(self.knowledge_dir.resolve()),
            "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        }

    def _import_candidates(self, raw_path: str, recursive: bool = True) -> tuple[Path, list[Path]]:
        self.repository.require_manage()
        source = Path(raw_path.strip().strip('"')).expanduser()
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
        if self._namespace and not is_within(source, self.knowledge_dir):
            raise KnowledgeError("不能扫描租户托管目录外的文件")
        if not source.exists():
            raise KnowledgeError("文件或目录不存在")
        if is_within(source, SECRETS_DIR):
            raise KnowledgeError("不能从密钥目录导入知识库")

        if source.is_file():
            candidates = [source]
        elif source.is_dir():
            pattern = "**/*" if recursive else "*"
            candidates = sorted(
                [
                    path for path in source.glob(pattern)
                    if path.is_file() and not is_within(path, SECRETS_DIR) and (not self._namespace or is_within(path, self.knowledge_dir))
                ],
                key=lambda path: str(path).casefold(),
            )
        else:
            raise KnowledgeError("路径必须是普通文件或目录")

        if len(candidates) > 500:
            raise KnowledgeError("单次最多导入 500 个文档")
        return source, candidates

    def import_path(
        self,
        raw_path: str,
        recursive: bool = True,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        source, candidates = self._import_candidates(raw_path, recursive)
        if progress:
            progress({"stage": "scanning", "total_count": len(candidates), "current_file": None})

        imported: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        for position, candidate in enumerate(candidates, 1):
            if progress:
                progress({"stage": "reading", "current_file": str(candidate), "processed_count": position - 1})
            issue = {"path": str(candidate), "display_name": normalize_filename(candidate.name)}
            if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
                skipped.append({**issue, "reason": "不支持的文件格式"})
                if progress:
                    progress({"stage": "processing", "current_file": str(candidate), "processed_count": position, "imported_count": len(imported), "skipped_count": len(skipped), "failed_count": len(failed)})
                continue
            try:
                if candidate.stat().st_size > MAX_UPLOAD_BYTES:
                    failed.append({**issue, "reason": f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"})
                    if progress:
                        progress({"stage": "processing", "current_file": str(candidate), "processed_count": position, "imported_count": len(imported), "skipped_count": len(skipped), "failed_count": len(failed)})
                    continue
                result = self.ingest(candidate.name, candidate.read_bytes(), source_path=candidate, progress=progress)
                imported.append(result)
            except KnowledgeDuplicateError as exc:
                skipped.append({**issue, "reason": str(exc)})
            except Exception as exc:
                reason = str(exc).strip() or "文档解析失败"
                failed.append({**issue, "reason": reason})
            if progress:
                progress({
                    "stage": "processing", "current_file": str(candidate), "processed_count": position,
                    "imported_count": len(imported), "skipped_count": len(skipped), "failed_count": len(failed),
                })

        return {
            "source_path": str(source),
            "imported": imported,
            "imported_count": len(imported),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "failed": failed,
            "failed_count": len(failed),
        }

    def list_documents(self) -> list[dict]:
        rows = self.repository.list_documents()
        for row in rows:
            row["warnings"] = decode_json(row.pop("warnings_json", "[]"), [])
            row["metadata"] = decode_json(row.pop("metadata_json", "{}"), {})
            row['metadata'].pop('_private_object', None)
        return rows

    def _object_content(self, row: dict, *, kind: str) -> bytes:
        reference = ObjectReference.from_dict(decode_json(row.get('metadata_json'), {})['_private_object'])
        if (reference.sha256 != row['sha256'] or reference.size != row['size']
                or not reference.key.startswith(f'tenants/{self._namespace}/{kind}/')):
            raise ObjectStoreError('Private object reference disagrees with publication manifest')
        if not self.object_store:
            raise ObjectStoreError('Private object storage not configured')
        return self.object_store.get(self._namespace, reference)

    def reparse(self, document_id: str) -> dict:
        self.repository.require_manage()
        row = self.repository.document(document_id)
        if not row:
            raise KnowledgeError("文档不存在")
        reference = decode_json(row.get('metadata_json'), {}).get('_private_object')
        if reference is not None:
            if not self.object_store:
                raise KnowledgeError('对象存储未配置，无法重新解析')
            self.repository.require_manage()
            data = self._object_content(row, kind='knowledge')
            self.repository.require_manage()
            return self.ingest(row['display_name'], data, force=True, _replace_document=(row['id'], row['version']))
        source = Path(row["source_path"]) if row["source_path"] else self.knowledge_dir / row["stored_name"]
        if self._namespace and not is_within(source, self.knowledge_dir):
            raise KnowledgeError("文档源文件不在租户托管目录")
        if not source.is_file():
            raise KnowledgeError("源文件不存在，无法重新解析")
        return self.ingest(row["display_name"], source.read_bytes(), source_path=source, force=True)

    def delete(self, document_id: str) -> None:
        self.repository.require_manage()
        row = self.repository.document(document_id)
        if not row:
            raise KnowledgeError("文档不存在")
        managed_path = (self.knowledge_dir / row["stored_name"]).resolve()
        try:
            managed_path.relative_to(self.knowledge_dir.resolve())
        except ValueError as exc:
            raise KnowledgeError("文档存储路径无效") from exc
        if not self.repository.delete_document(document_id, row["version"]):
            raise KnowledgeError("文档已被更新，请刷新后重试")
        self._cleanup_asset_versions(document_id)
        if managed_path.exists():
            managed_path.unlink()
        else:
            # 兼容改版前保存在 data/uploads 中的哈希命名文档。
            (self.legacy_upload_dir / row["stored_name"]).unlink(missing_ok=True)

    @staticmethod
    def _public_asset(row: dict) -> dict:
        return {
            "id": row["id"],
            "url": f"/api/knowledge/assets/{row['id']}",
            "media_type": row["media_type"],
            "width": row["width"],
            "height": row["height"],
            "location": row["location"],
            "alt_text": row["alt_text"],
        }

    def list_assets(self, document_id: str) -> list[dict]:
        return [self._public_asset(row) for row in self.repository.assets([document_id])]

    def asset_file(self, asset_id: str) -> tuple[Path, str]:
        row = self.repository.asset(asset_id)
        if not row:
            raise KnowledgeError("图片资产不存在")
        if '_private_object' in decode_json(row.get('metadata_json'), {}):
            raise KnowledgeError('远程资产必须通过鉴权内容接口读取')
        path = (self.asset_dir / row["stored_name"]).resolve()
        if not is_within(path, self.asset_dir) or not path.is_file():
            raise KnowledgeError("图片资产不存在")
        if row["media_type"] != "image/png" or path.suffix.lower() != ".png":
            raise KnowledgeError("图片资产类型无效")
        return path, row["media_type"]

    def asset_content(self, asset_id: str) -> tuple[bytes, str]:
        row = self.repository.asset(asset_id)  # Authorization before any remote I/O.
        if not row or row['media_type'] != 'image/png':
            raise KnowledgeError('图片资产不存在或类型无效')
        reference = decode_json(row.get('metadata_json'), {}).get('_private_object')
        if reference is None:
            path, media_type = self.asset_file(asset_id)
            return path.read_bytes(), media_type
        if not self.object_store:
            raise ObjectStoreError('Private object storage not configured')
        data = self._object_content(row, kind='assets')
        if self.repository.asset(asset_id) != row:
            raise KnowledgeError('图片资产在读取期间已变化')
        return data, row['media_type']

    def rebuild(self) -> int:
        return self.repository.rebuild(tokenize, TOKENIZER_VERSION)

    def ensure_index_current(self) -> int:
        return 0 if self.repository.setting("knowledge_tokenizer_version") == TOKENIZER_VERSION else self.rebuild()

    def active_index_id(self):
        if isinstance(self.vector_retriever, RepositoryVectorRetriever):
            profile = self.repository.active_vector_profile()
            return profile['index_id'] if profile is not None else None
        return None

    def supports_rag_routing(self):
        return (isinstance(self.repository,KnowledgeRepository)
            and self.repository.engine.dialect.name=='postgresql'
            and isinstance(self.vector_retriever,RepositoryVectorRetriever))

    def for_route(self,route):
        """Per-run copy: model gateways may be shared, mutable routing may not."""
        from copy import copy
        if not self.supports_rag_routing():
            raise ValueError('Run routing requires the PostgreSQL vector repository')
        clone=copy(self)
        clone.repository=self.repository.with_route(route)
        clone.vector_retriever=copy(self.vector_retriever)
        clone.vector_retriever.repository=clone.repository
        from app.rag_local import LocalEmbeddingGateway,LocalReranker
        from app.local_inference_usage import AuditInferenceMeter
        gateway=clone.vector_retriever.embedding_gateway
        if isinstance(gateway,LocalEmbeddingGateway):
            clone.vector_retriever.embedding_gateway=gateway.with_meter(AuditInferenceMeter.for_knowledge(clone.repository,EMBEDDING_WEIGHT))
        if isinstance(clone.reranker,LocalReranker):
            clone.reranker=clone.reranker.with_meter(AuditInferenceMeter.for_knowledge(clone.repository,RERANKER_WEIGHT))
        if isinstance(self.lexical_retriever,RepositoryLexicalRetriever):
            clone.lexical_retriever=RepositoryLexicalRetriever(clone.repository)
        return clone

    def require_index(self, index_id):
        if self.active_index_id() != index_id:
            raise KnowledgeIndexChanged()

    def search(self, query: str, limit: int = 6, *, index_id=None, pinned=False) -> list[dict]:
        if pinned:
            self.require_index(index_id)
        self.last_degraded_reason = None
        base_terms = _word_terms(query)
        expanded_values, synonym_groups = expand_query(query, base_terms, repository=self.repository)
        match_terms: list[str] = []
        for value in expanded_values:
            match_terms.extend(tokenize(value).split())
        match_terms = list(dict.fromkeys(match_terms))
        if not match_terms:
            return []
        candidate_limit = min(max(limit * 5, 20), 100)
        candidate_batch = self.lexical_retriever.candidates(match_terms, candidate_limit)
        fts_rows, character_rows = candidate_batch.ranked, candidate_batch.character_pool
        vector_rows = []
        if self.vector_retriever is not None:
            try:
                vector_rows = (self.vector_retriever.candidates(query, limit=candidate_limit,
                    index_id=index_id) if isinstance(self.vector_retriever, RepositoryVectorRetriever)
                    else self.vector_retriever.candidates(query, limit=candidate_limit))
            except (RagModelUnavailable, RagConfigurationError) as exc:
                self.last_degraded_reason = type(exc).__name__
                logger.warning('RAG vector degraded reason=%s', self.last_degraded_reason)
        if pinned:
            self.require_index(index_id)
        fusion_scores = {}
        if vector_rows:
            tenant = self.repository.storage_namespace()
            def ranked(rows, channel):
                return [RankedCandidate(
                    tenant_id=row['tenant_id'], document_id=row['document_id'],
                    document_version=row['document_version'], chunk_id=str(row['id']),
                    content_sha256=row.get('content_sha256') or hashlib.sha256(
                        row['content'].encode('utf-8')).hexdigest(), channel=channel, rank=rank)
                    for rank, row in enumerate(rows, 1)]
            with operation_scope('rag.fuse', rag_stage='fuse'):
                fused = reciprocal_rank_fusion({'lexical': ranked(fts_rows, 'lexical'),
                    'vector': ranked(vector_rows, 'vector')}, tenant_id=tenant,
                    limit=candidate_limit)
            fusion_scores = {item.candidate.chunk_id: item.score * 60 for item in fused}
        normalized_query = re.sub(r"\s+", "", query.casefold())
        query_terms = set(base_terms)
        query_ngrams = set(_character_ngrams(query))
        model_terms = set(re.findall(r"[a-z]{1,12}[a-z0-9.-]*\d[a-z0-9.-]*", query.casefold()))
        candidates = {row["id"]: dict(row) for row in fts_rows}
        for rank, row in enumerate(vector_rows, 1):
            candidates.setdefault(row['id'], {**row, 'score': 0.0, 'score_kind': 'vector'})
        semantic_scores: dict[int, float] = {}
        for row in character_rows:
            item = dict(row)
            haystack = f"{item.get('display_name', '')} {item.get('section', '')} {item['content']}"
            grams = set(_character_ngrams(haystack))
            similarity = len(query_ngrams & grams) / math.sqrt(max(len(query_ngrams) * len(grams), 1))
            if similarity >= 0.04:
                semantic_scores[item["id"]] = similarity
                candidates.setdefault(item["id"], {**item, "score": 0.0, "score_kind": "character_fallback"})
        ranked: list[dict] = []
        for item in candidates.values():
            haystack = f"{item.get('display_name', '')} {item.get('section', '')} {item['content']}".casefold()
            compact = re.sub(r"\s+", "", haystack)
            matched = sum(1 for term in query_terms if term in haystack)
            lexical = matched / max(len(query_terms), 1)
            exact_boost = 1.5 if normalized_query and normalized_query in compact else 0.0
            title_boost = 0.35 if any(term in (item.get("section") or "").casefold() for term in query_terms) else 0.0
            model_boost = 0.75 if model_terms and any(term in haystack for term in model_terms) else 0.0
            semantic = semantic_scores.get(item["id"], 0.0)
            raw_score = -float(item.get("score") or 0)
            lexical_boost = min(max(raw_score if item.get("score_kind") == "postgres_ts_rank" else raw_score / 10, 0.0), 0.5)
            fusion_boost = fusion_scores.get(str(item['id']), 0.0)
            relevance = lexical + exact_boost + title_boost + model_boost + semantic * 0.8 + lexical_boost + fusion_boost
            item["relevance"] = round(relevance, 4)
            item["ranking"] = {
                "term_coverage": round(lexical, 4),
                "lexical_score_kind": item.get("score_kind"),
                "phrase_boost": exact_boost,
                "section_boost": title_boost,
                "model_boost": model_boost,
                "character_similarity": round(semantic, 4),
                "matched_synonyms": synonym_groups,
                "fusion_boost": round(fusion_boost, 4),
            }
            ranked.append(item)
        ranked.sort(key=lambda item: (-item["relevance"], item["score"]))
        if self.reranker is not None and ranked:
            pool = ranked[:min(16, len(ranked))]
            try:
                with operation_scope('rag.rerank', rag_stage='rerank'):
                    scores = self.reranker.score(query, [item['content'] for item in pool])
                if len(scores) != len(pool) or any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
                    raise ValueError('Invalid local Reranker scores')
                for item, score in zip(pool, scores):
                    item['ranking']['reranker_score'] = round(score, 4)
                    item['relevance'] = round(item['relevance'] + score, 4)
                ranked.sort(key=lambda item: (-item['relevance'], item['score']))
            except (AuthenticationError,AuthorizationError,KnowledgeIndexChanged,SQLAlchemyError,LocalInferenceAccountingError):
                raise
            except (RuntimeError, ValueError) as exc:
                self.last_degraded_reason = type(exc).__name__
                logger.warning('RAG reranker degraded reason=%s', self.last_degraded_reason)
        # Keep a bounded reserve until the final scoped version/visibility
        # check. Otherwise a revoked top hit leaves a hole even when the next
        # ranked candidate is still current and authorized.
        pool_limit = candidate_limit if hasattr(self.repository, 'filter_current_results') else limit
        results = [item for item in ranked if item["relevance"] >= 0.12][:pool_limit]
        if not results:
            return []
        document_ids = list(dict.fromkeys(item["document_id"] for item in results))
        asset_rows = self.repository.assets(document_ids)
        assets_by_document: dict[str, list[dict]] = {}
        for asset in asset_rows:
            try:
                asset["metadata"] = decode_json(asset.pop("metadata_json", "{}"), {})
            except (TypeError, json.JSONDecodeError):
                asset["metadata"] = {}
            assets_by_document.setdefault(asset["document_id"], []).append(asset)
        for item in results:
            try:
                metadata = decode_json(item.pop("metadata_json", "{}"), {})
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            def metadata_values(value: dict, key: str) -> set:
                values = {value[key]} if key in value and value[key] is not None else set()
                for source_block in value.get("source_blocks", []):
                    if isinstance(source_block, dict) and source_block.get(key) is not None:
                        values.add(source_block[key])
                return values
            asset_indexes = metadata_values(metadata, "asset_index")
            pages = metadata_values(metadata, "page")
            slides = metadata_values(metadata, "slide")
            matches: list[dict] = []
            for asset in assets_by_document.get(item["document_id"], []):
                asset_metadata = asset["metadata"]
                exact = asset["asset_index"] in asset_indexes
                same_page = asset_metadata.get("page") is not None and asset_metadata.get("page") in pages
                same_slide = asset_metadata.get("slide") is not None and asset_metadata.get("slide") in slides
                if exact or same_page or same_slide:
                    matches.append(self._public_asset(asset))
            item["images"] = matches[:3]
        if hasattr(self.repository, 'filter_current_results'):
            return self.repository.filter_current_results(results)[:limit]
        return results[:limit]

    def sources_current(self, sources: list[dict]) -> bool:
        """Recheck cited source identities before committing an Agent answer."""
        if not sources:
            return True
        if not hasattr(self.repository, 'filter_current_results'):
            return True  # Local MVP has no PostgreSQL versioned citation contract.
        return len(self.repository.filter_current_results(sources)) == len(sources)
