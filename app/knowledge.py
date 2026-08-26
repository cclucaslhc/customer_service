import hashlib
import json
import math
import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Callable

import jieba

from app.database import connect, utc_now
from app.knowledge_chunking import chunk_document, chunks_as_dicts
from app.knowledge_models import CHUNKER_VERSION, PARSER_VERSION, ContentBlock, DocumentParseError, ParsedDocument
from app.knowledge_parsers import parse_document as parse_structured_document
from app.knowledge_terms import STOPWORDS, expand_query
from app.settings import ALLOWED_EXTENSIONS, KNOWLEDGE_ASSET_DIR, KNOWLEDGE_DIR, LEGACY_UPLOAD_DIR, MAX_UPLOAD_BYTES, ROOT, SECRETS_DIR


class KnowledgeError(ValueError):
    pass


class KnowledgeDuplicateError(KnowledgeError):
    pass


TOKENIZER_VERSION = "2.0"


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
    def _asset_version_dir(self, document_id: str, version: int) -> Path:
        directory = (KNOWLEDGE_ASSET_DIR / document_id / f"v{version}").resolve()
        if not is_within(directory, KNOWLEDGE_ASSET_DIR):
            raise KnowledgeError("图片资产存储路径无效")
        return directory

    def _store_assets(self, parsed: ParsedDocument, document_id: str, version: int) -> list[dict]:
        if not parsed.assets:
            return []
        directory = self._asset_version_dir(document_id, version)
        directory.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        stored: list[dict] = []
        try:
            for asset in parsed.assets:
                asset_id = str(uuid.uuid4())
                filename = f"{asset_id}{asset.extension}"
                path = directory / filename
                path.write_bytes(asset.data)
                stored.append({
                    "id": asset_id,
                    "document_id": document_id,
                    "asset_index": asset.asset_index,
                    "stored_name": str(path.relative_to(KNOWLEDGE_ASSET_DIR)).replace("\\", "/"),
                    "media_type": asset.media_type,
                    "size": len(asset.data),
                    "width": asset.width,
                    "height": asset.height,
                    "location": asset.location,
                    "alt_text": asset.alt_text,
                    "sha256": hashlib.sha256(asset.data).hexdigest(),
                    "metadata_json": json.dumps(asset.metadata, ensure_ascii=False),
                    "created_at": now,
                })
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return stored

    def _cleanup_asset_versions(self, document_id: str, keep_version: int | None = None) -> None:
        root = (KNOWLEDGE_ASSET_DIR / document_id).resolve()
        if not is_within(root, KNOWLEDGE_ASSET_DIR) or not root.exists():
            return
        for child in root.iterdir():
            if keep_version is not None and child.name == f"v{keep_version}":
                continue
            if child.is_dir() and is_within(child, root):
                shutil.rmtree(child)
        if keep_version is None and root.exists() and not any(root.iterdir()):
            root.rmdir()

    def _managed_path(self, filename: str, digest: str, source_path: Path | None = None) -> Path:
        if source_path:
            resolved_source = source_path.resolve()
            try:
                resolved_source.relative_to(KNOWLEDGE_DIR.resolve())
                return resolved_source
            except ValueError:
                pass

        safe_name = Path(filename).name
        destination = KNOWLEDGE_DIR / safe_name
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            destination = KNOWLEDGE_DIR / f"{Path(safe_name).stem}-{digest[:8]}{Path(safe_name).suffix.lower()}"
        return destination

    def ingest(
        self,
        filename: str,
        data: bytes,
        source_path: Path | None = None,
        force: bool = False,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        display_name = normalize_filename(filename)
        extension = Path(display_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise KnowledgeError(f"不支持扩展名 {extension or '（无扩展名）'}")
        if len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeError(f"文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if not data:
            raise KnowledgeError("文件为空")
        digest = hashlib.sha256(data).hexdigest()
        source_value = str(source_path.resolve()) if source_path else None
        with connect() as db:
            existing = db.execute(
                "SELECT * FROM documents WHERE source_path=? ORDER BY version DESC LIMIT 1",
                (source_value,),
            ).fetchone() if source_value else None
            duplicate = db.execute("SELECT id,source_path FROM documents WHERE sha256=?", (digest,)).fetchone()
        if duplicate and (existing is None or duplicate["id"] != existing["id"]):
            raise KnowledgeDuplicateError("该文件已经建立索引")
        if existing is not None and existing["sha256"] == digest and not force:
            raise KnowledgeDuplicateError("该文件内容未变化，已跳过")
        doc_id = existing["id"] if existing else str(uuid.uuid4())
        version = int(existing["version"] or 1) + 1 if existing else 1
        previous_managed_path = (KNOWLEDGE_DIR / existing["stored_name"]).resolve() if existing else None
        try:
            if progress:
                progress({"stage": "parsing", "current_file": display_name})
            try:
                parsed = parse_structured_document(display_name, data)
            except DocumentParseError as exc:
                raise KnowledgeError(str(exc)) from exc
            if progress:
                progress({"stage": "chunking", "current_file": display_name})
            chunks = chunks_as_dicts(chunk_document(parsed))
            if not chunks:
                raise KnowledgeError("文档没有可索引的文字")
            path = self._managed_path(display_name, digest, source_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if source_path is None or path.resolve() != source_path.resolve():
                path.write_bytes(data)
            stored_name = str(path.relative_to(KNOWLEDGE_DIR))
            source_value = source_value or str(path.resolve())
            source_mtime = source_path.stat().st_mtime if source_path and source_path.exists() else path.stat().st_mtime
            now = utc_now()
            stored_assets = self._store_assets(parsed, doc_id, version)
            parsed.metadata["asset_count"] = len(stored_assets)
            if progress:
                progress({"stage": "indexing", "current_file": display_name})
            with connect() as db:
                if existing:
                    db.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,))
                    db.execute("DELETE FROM knowledge_assets WHERE document_id=?", (doc_id,))
                    db.execute(
                        """UPDATE documents SET
                               display_name=?,stored_name=?,extension=?,size=?,sha256=?,status='ready',
                               error_message=NULL,chunk_count=?,detected_type=?,source_path=?,source_mtime=?,
                               parser_version=?,chunker_version=?,indexed_at=?,version=?,warnings_json=?,metadata_json=?
                           WHERE id=?""",
                        (
                            display_name, stored_name, extension, len(data), digest, len(chunks),
                            parsed.document_type, source_value, source_mtime, PARSER_VERSION, CHUNKER_VERSION,
                            now, version,
                            json.dumps(parsed.warnings, ensure_ascii=False),
                            json.dumps(parsed.metadata, ensure_ascii=False), doc_id,
                        ),
                    )
                else:
                    db.execute(
                        """INSERT INTO documents(
                           id,display_name,stored_name,extension,size,sha256,status,chunk_count,
                           detected_type,source_path,source_mtime,parser_version,chunker_version,
                           indexed_at,version,warnings_json,metadata_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            doc_id, display_name, stored_name, extension, len(data), digest, "ready", len(chunks),
                            parsed.document_type, source_value, source_mtime, PARSER_VERSION, CHUNKER_VERSION,
                            now, version, json.dumps(parsed.warnings, ensure_ascii=False),
                            json.dumps(parsed.metadata, ensure_ascii=False), now,
                        ),
                    )
                for index, chunk in enumerate(chunks):
                    db.execute(
                        """INSERT INTO knowledge_chunks(
                               document_id,chunk_index,section,location,content,tokenized,
                               block_type,metadata_json,char_count
                           ) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (
                            doc_id, index, chunk["section"], chunk["location"], chunk["content"],
                            tokenize(chunk["content"]), chunk["block_type"],
                            json.dumps(chunk["metadata"], ensure_ascii=False), len(chunk["content"]),
                        ),
                    )
                for asset in stored_assets:
                    db.execute(
                        """INSERT INTO knowledge_assets(
                               id,document_id,asset_index,stored_name,media_type,size,width,height,
                               location,alt_text,sha256,metadata_json,created_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        tuple(asset[key] for key in (
                            "id", "document_id", "asset_index", "stored_name", "media_type", "size",
                            "width", "height", "location", "alt_text", "sha256", "metadata_json", "created_at",
                        )),
                    )
            if previous_managed_path and previous_managed_path != path.resolve() and is_within(previous_managed_path, KNOWLEDGE_DIR):
                previous_managed_path.unlink(missing_ok=True)
            self._cleanup_asset_versions(doc_id, keep_version=version)
        except Exception:
            cleanup_path = locals().get("path")
            if cleanup_path and (source_path is None or cleanup_path.resolve() != source_path.resolve()):
                if not existing or cleanup_path.resolve() != previous_managed_path:
                    cleanup_path.unlink(missing_ok=True)
            asset_version_path = self._asset_version_dir(doc_id, version)
            if asset_version_path.exists():
                shutil.rmtree(asset_version_path, ignore_errors=True)
            raise
        return {
            "id": doc_id, "display_name": display_name, "status": "ready",
            "chunk_count": len(chunks), "detected_type": parsed.document_type,
            "version": version, "asset_count": len(stored_assets),
            "warnings": parsed.warnings,
        }

    def location(self) -> dict:
        return {
            "path": str(KNOWLEDGE_DIR.resolve()),
            "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        }

    def _import_candidates(self, raw_path: str, recursive: bool = True) -> tuple[Path, list[Path]]:
        source = Path(raw_path.strip().strip('"')).expanduser()
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
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
                    if path.is_file() and not is_within(path, SECRETS_DIR)
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
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                """SELECT d.*,(SELECT count(*) FROM knowledge_assets a WHERE a.document_id=d.id) asset_count
                   FROM documents d ORDER BY d.created_at DESC"""
            ).fetchall()]
        for row in rows:
            row["warnings"] = json.loads(row.pop("warnings_json", "[]") or "[]")
            row["metadata"] = json.loads(row.pop("metadata_json", "{}") or "{}")
        return rows

    def reparse(self, document_id: str) -> dict:
        with connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise KnowledgeError("文档不存在")
        source = Path(row["source_path"]) if row["source_path"] else KNOWLEDGE_DIR / row["stored_name"]
        if not source.is_file():
            raise KnowledgeError("源文件不存在，无法重新解析")
        return self.ingest(source.name, source.read_bytes(), source_path=source, force=True)

    def delete(self, document_id: str) -> None:
        with connect() as db:
            row = db.execute("SELECT stored_name FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row:
                raise KnowledgeError("文档不存在")
        managed_path = (KNOWLEDGE_DIR / row["stored_name"]).resolve()
        try:
            managed_path.relative_to(KNOWLEDGE_DIR.resolve())
        except ValueError as exc:
            raise KnowledgeError("文档存储路径无效") from exc
        with connect() as db:
            db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        self._cleanup_asset_versions(document_id)
        if managed_path.exists():
            managed_path.unlink()
        else:
            # 兼容改版前保存在 data/uploads 中的哈希命名文档。
            (LEGACY_UPLOAD_DIR / row["stored_name"]).unlink(missing_ok=True)

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
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM knowledge_assets WHERE document_id=? ORDER BY asset_index",
                (document_id,),
            ).fetchall()
        return [self._public_asset(dict(row)) for row in rows]

    def asset_file(self, asset_id: str) -> tuple[Path, str]:
        with connect() as db:
            row = db.execute(
                """SELECT a.stored_name,a.media_type FROM knowledge_assets a
                   JOIN documents d ON d.id=a.document_id
                   WHERE a.id=? AND d.status='ready'""",
                (asset_id,),
            ).fetchone()
        if not row:
            raise KnowledgeError("图片资产不存在")
        path = (KNOWLEDGE_ASSET_DIR / row["stored_name"]).resolve()
        if not is_within(path, KNOWLEDGE_ASSET_DIR) or not path.is_file():
            raise KnowledgeError("图片资产不存在")
        if row["media_type"] != "image/png" or path.suffix.lower() != ".png":
            raise KnowledgeError("图片资产类型无效")
        return path, row["media_type"]

    def rebuild(self) -> int:
        with connect() as db:
            rows = db.execute("SELECT id, content FROM knowledge_chunks").fetchall()
            for row in rows:
                db.execute("UPDATE knowledge_chunks SET tokenized=? WHERE id=?", (tokenize(row["content"]), row["id"]))
            db.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES('knowledge_tokenizer_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (TOKENIZER_VERSION,),
            )
        return len(rows)

    def ensure_index_current(self) -> int:
        with connect() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key='knowledge_tokenizer_version'").fetchone()
        return 0 if row and row["value"] == TOKENIZER_VERSION else self.rebuild()

    def search(self, query: str, limit: int = 6) -> list[dict]:
        base_terms = _word_terms(query)
        expanded_values, synonym_groups = expand_query(query, base_terms)
        match_terms: list[str] = []
        for value in expanded_values:
            match_terms.extend(tokenize(value).split())
        match_terms = list(dict.fromkeys(match_terms))
        if not match_terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in match_terms[:64])
        candidate_limit = min(max(limit * 5, 20), 100)
        with connect() as db:
            fts_rows = db.execute(
                """
                SELECT c.id,c.content,c.section,c.location,c.block_type,c.metadata_json,d.id document_id,d.display_name,
                       bm25(knowledge_fts) score
                FROM knowledge_fts
                JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE knowledge_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (match_query, candidate_limit),
            ).fetchall()
            semantic_rows = db.execute(
                """SELECT c.id,c.content,c.section,c.location,c.block_type,c.metadata_json,d.id document_id,d.display_name
                   FROM knowledge_chunks c JOIN documents d ON d.id=c.document_id
                   WHERE d.status='ready' ORDER BY c.id DESC LIMIT 5000"""
            ).fetchall()
        normalized_query = re.sub(r"\s+", "", query.casefold())
        query_terms = set(base_terms)
        query_ngrams = set(_character_ngrams(query))
        model_terms = set(re.findall(r"[a-z]{1,12}[a-z0-9.-]*\d[a-z0-9.-]*", query.casefold()))
        candidates = {row["id"]: dict(row) for row in fts_rows}
        semantic_scores: dict[int, float] = {}
        for row in semantic_rows:
            item = dict(row)
            haystack = f"{item.get('display_name', '')} {item.get('section', '')} {item['content']}"
            grams = set(_character_ngrams(haystack))
            similarity = len(query_ngrams & grams) / math.sqrt(max(len(query_ngrams) * len(grams), 1))
            if similarity >= 0.04:
                semantic_scores[item["id"]] = similarity
                candidates.setdefault(item["id"], {**item, "score": 0.0})
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
            bm25_boost = min(max(-float(item.get("score") or 0) / 10, 0.0), 0.5)
            relevance = lexical + exact_boost + title_boost + model_boost + semantic * 0.8 + bm25_boost
            item["relevance"] = round(relevance, 4)
            item["ranking"] = {
                "term_coverage": round(lexical, 4),
                "phrase_boost": exact_boost,
                "section_boost": title_boost,
                "model_boost": model_boost,
                "character_similarity": round(semantic, 4),
                "matched_synonyms": synonym_groups,
            }
            ranked.append(item)
        ranked.sort(key=lambda item: (-item["relevance"], item["score"]))
        results = [item for item in ranked if item["relevance"] >= 0.12][:limit]
        if not results:
            return []
        document_ids = list(dict.fromkeys(item["document_id"] for item in results))
        placeholders = ",".join("?" for _ in document_ids)
        with connect() as db:
            asset_rows = [dict(row) for row in db.execute(
                f"SELECT * FROM knowledge_assets WHERE document_id IN ({placeholders}) ORDER BY asset_index",
                document_ids,
            ).fetchall()]
        assets_by_document: dict[str, list[dict]] = {}
        for asset in asset_rows:
            try:
                asset["metadata"] = json.loads(asset.pop("metadata_json", "{}") or "{}")
            except (TypeError, json.JSONDecodeError):
                asset["metadata"] = {}
            assets_by_document.setdefault(asset["document_id"], []).append(asset)
        for item in results:
            try:
                metadata = json.loads(item.pop("metadata_json", "{}") or "{}")
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
        return results
