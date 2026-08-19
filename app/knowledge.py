import hashlib
import io
import re
import unicodedata
import uuid
from pathlib import Path

import jieba
from docx import Document as DocxDocument
from pypdf import PdfReader

from app.database import connect, utc_now
from app.settings import ALLOWED_EXTENSIONS, KNOWLEDGE_DIR, LEGACY_UPLOAD_DIR, MAX_UPLOAD_BYTES, ROOT, SECRETS_DIR


class KnowledgeError(ValueError):
    pass


class KnowledgeDuplicateError(KnowledgeError):
    pass


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


def tokenize(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    terms = [term.strip() for term in jieba.cut_for_search(normalized) if term.strip()]
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9._-]*", normalized)
    return " ".join(dict.fromkeys([*terms, *ascii_terms]))


def parse_document(data: bytes, extension: str) -> list[tuple[str, str]]:
    if extension in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030")
        return [("正文", text)]
    if extension == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        pages = [(f"第 {index + 1} 页", page.extract_text() or "") for index, page in enumerate(reader.pages)]
        if not any(text.strip() for _, text in pages):
            raise KnowledgeError("PDF 未提取到文本，可能是扫描件；首版暂不支持 OCR")
        return pages
    if extension == ".docx":
        doc = DocxDocument(io.BytesIO(data))
        sections: list[tuple[str, str]] = []
        title = "正文"
        buffer: list[str] = []
        for paragraph in doc.paragraphs:
            value = paragraph.text.strip()
            if not value:
                continue
            if paragraph.style and paragraph.style.name.lower().startswith("heading"):
                if buffer:
                    sections.append((title, "\n".join(buffer)))
                title, buffer = value, []
            else:
                buffer.append(value)
        if buffer:
            sections.append((title, "\n".join(buffer)))
        return sections
    raise KnowledgeError("不支持的文件格式")


def chunk_sections(sections: list[tuple[str, str]], target_chars: int = 900) -> list[dict]:
    chunks: list[dict] = []
    for location, text in sections:
        paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 1 > target_chars:
                chunks.append({"section": location, "location": location, "content": current})
                current = paragraph
            else:
                current = f"{current}\n{paragraph}".strip()
        if current:
            chunks.append({"section": location, "location": location, "content": current})
    return chunks


class KnowledgeService:
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

    def ingest(self, filename: str, data: bytes, source_path: Path | None = None) -> dict:
        display_name = normalize_filename(filename)
        extension = Path(display_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise KnowledgeError("仅支持 PDF、DOCX、Markdown 和 TXT")
        if len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeError("文件不能超过 20 MB")
        if not data:
            raise KnowledgeError("文件为空")
        digest = hashlib.sha256(data).hexdigest()
        with connect() as db:
            duplicate = db.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
        if duplicate:
            raise KnowledgeDuplicateError("该文件已经建立索引")
        doc_id = str(uuid.uuid4())
        path = self._managed_path(display_name, digest, source_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if source_path is None or path.resolve() != source_path.resolve():
            path.write_bytes(data)
        stored_name = str(path.relative_to(KNOWLEDGE_DIR))
        try:
            chunks = chunk_sections(parse_document(data, extension))
            if not chunks:
                raise KnowledgeError("文档没有可索引的文字")
            with connect() as db:
                db.execute(
                    "INSERT INTO documents(id,display_name,stored_name,extension,size,sha256,status,chunk_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (doc_id, display_name, stored_name, extension, len(data), digest, "ready", len(chunks), utc_now()),
                )
                for index, chunk in enumerate(chunks):
                    db.execute(
                        "INSERT INTO knowledge_chunks(document_id,chunk_index,section,location,content,tokenized) VALUES(?,?,?,?,?,?)",
                        (doc_id, index, chunk["section"], chunk["location"], chunk["content"], tokenize(chunk["content"])),
                    )
        except Exception:
            if source_path is None or path.resolve() != source_path.resolve():
                path.unlink(missing_ok=True)
            raise
        return {"id": doc_id, "display_name": display_name, "status": "ready", "chunk_count": len(chunks)}

    def location(self) -> dict:
        return {
            "path": str(KNOWLEDGE_DIR.resolve()),
            "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        }

    def import_path(self, raw_path: str, recursive: bool = True) -> dict:
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

        imported: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        for candidate in candidates:
            issue = {"path": str(candidate), "display_name": normalize_filename(candidate.name)}
            if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
                skipped.append({**issue, "reason": "不支持的文件格式"})
                continue
            try:
                if candidate.stat().st_size > MAX_UPLOAD_BYTES:
                    failed.append({**issue, "reason": "文件不能超过 20 MB"})
                    continue
                result = self.ingest(candidate.name, candidate.read_bytes(), source_path=candidate)
                imported.append(result)
            except KnowledgeDuplicateError as exc:
                skipped.append({**issue, "reason": str(exc)})
            except Exception as exc:
                reason = str(exc).strip() or "文档解析失败"
                failed.append({**issue, "reason": reason})

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
            return [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()]

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
        if managed_path.exists():
            managed_path.unlink()
        else:
            # 兼容改版前保存在 data/uploads 中的哈希命名文档。
            (LEGACY_UPLOAD_DIR / row["stored_name"]).unlink(missing_ok=True)

    def rebuild(self) -> int:
        with connect() as db:
            rows = db.execute("SELECT id, content FROM knowledge_chunks").fetchall()
            for row in rows:
                db.execute("UPDATE knowledge_chunks SET tokenized=? WHERE id=?", (tokenize(row["content"]), row["id"]))
            db.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
        return len(rows)

    def search(self, query: str, limit: int = 6) -> list[dict]:
        terms = tokenize(query).split()
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:16])
        with connect() as db:
            rows = db.execute(
                """
                SELECT c.id,c.content,c.section,c.location,d.id document_id,d.display_name,
                       bm25(knowledge_fts) score
                FROM knowledge_fts
                JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid
                JOIN documents d ON d.id=c.document_id
                WHERE knowledge_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (match_query, limit),
            ).fetchall()
        return [dict(row) for row in rows]
