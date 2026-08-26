import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.settings import DATABASE_BUSY_TIMEOUT_MS, DATABASE_PATH, ensure_directories
from app.prompting import DEFAULT_BUSINESS_PROMPT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_directories()
    conn = sqlite3.connect(
        DATABASE_PATH,
        timeout=DATABASE_BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DATABASE_BUSY_TIMEOUT_MS}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    # WAL lets readers continue while another request is committing. Setting it once
    # during startup avoids changing journal mode on every short-lived connection.
    ensure_directories()
    bootstrap = sqlite3.connect(DATABASE_PATH, timeout=DATABASE_BUSY_TIMEOUT_MS / 1000)
    try:
        bootstrap.execute(f"PRAGMA busy_timeout = {DATABASE_BUSY_TIMEOUT_MS}")
        bootstrap.execute("PRAGMA journal_mode = WAL")
        bootstrap.execute("PRAGMA synchronous = NORMAL")
    finally:
        bootstrap.close()
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_configs (
                provider TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                encrypted_key BLOB,
                nonce BLOB,
                updated_at TEXT NOT NULL,
                last_test_status TEXT,
                last_test_message TEXT,
                last_test_at TEXT
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                management_status TEXT NOT NULL DEFAULT 'open',
                assignee TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                archived_at TEXT,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete',
                provider TEXT,
                model TEXT,
                feedback TEXT CHECK(feedback IN ('up', 'down')),
                citations_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                prompt_version_id TEXT,
                error_code TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                error_message TEXT,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                detected_type TEXT,
                source_path TEXT,
                source_mtime REAL,
                parser_version TEXT NOT NULL DEFAULT '1.0',
                chunker_version TEXT NOT NULL DEFAULT '1.0',
                indexed_at TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                section TEXT,
                location TEXT,
                content TEXT NOT NULL,
                tokenized TEXT NOT NULL,
                block_type TEXT NOT NULL DEFAULT 'paragraph',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                char_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS knowledge_assets (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                asset_index INTEGER NOT NULL,
                stored_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                location TEXT NOT NULL,
                alt_text TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(document_id, asset_index)
            );
            CREATE TABLE IF NOT EXISTS knowledge_jobs (
                id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                recursive INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'pending',
                total_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                imported_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                current_file TEXT,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                tokenized,
                content='knowledge_chunks',
                content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
                INSERT INTO knowledge_fts(rowid, tokenized) VALUES (new.id, new.tokenized);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, tokenized)
                VALUES('delete', old.id, old.tokenized);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, tokenized)
                VALUES('delete', old.id, old.tokenized);
                INSERT INTO knowledge_fts(rowid, tokenized) VALUES (new.id, new.tokenized);
            END;
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES chat_sessions(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL UNIQUE,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                change_note TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE TABLE IF NOT EXISTS session_notes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_run_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                actor TEXT NOT NULL DEFAULT 'local_admin',
                created_at TEXT NOT NULL
            );
            """
        )
        session_columns = {row["name"] for row in db.execute("PRAGMA table_info(chat_sessions)").fetchall()}
        if "title" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN title TEXT")
        if "archived_at" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN archived_at TEXT")
        if "deleted_at" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN deleted_at TEXT")
        if "management_status" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN management_status TEXT NOT NULL DEFAULT 'open'")
        if "assignee" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN assignee TEXT")
        if "tags_json" not in session_columns:
            db.execute("ALTER TABLE chat_sessions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
        run_columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_runs)").fetchall()}
        if "prompt_version_id" not in run_columns:
            db.execute("ALTER TABLE agent_runs ADD COLUMN prompt_version_id TEXT")
        message_columns = {row["name"] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
        if "feedback" not in message_columns:
            db.execute("ALTER TABLE messages ADD COLUMN feedback TEXT")
        contact_columns = {row["name"] for row in db.execute("PRAGMA table_info(contacts)").fetchall()}
        if "status" not in contact_columns:
            db.execute("ALTER TABLE contacts ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        if "note" not in contact_columns:
            db.execute("ALTER TABLE contacts ADD COLUMN note TEXT")
        document_columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)").fetchall()}
        document_additions = {
            "detected_type": "TEXT",
            "source_path": "TEXT",
            "source_mtime": "REAL",
            "parser_version": "TEXT NOT NULL DEFAULT '1.0'",
            "chunker_version": "TEXT NOT NULL DEFAULT '1.0'",
            "indexed_at": "TEXT",
            "version": "INTEGER NOT NULL DEFAULT 1",
            "warnings_json": "TEXT NOT NULL DEFAULT '[]'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, declaration in document_additions.items():
            if column not in document_columns:
                db.execute(f"ALTER TABLE documents ADD COLUMN {column} {declaration}")
        chunk_columns = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_chunks)").fetchall()}
        chunk_additions = {
            "block_type": "TEXT NOT NULL DEFAULT 'paragraph'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "char_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, declaration in chunk_additions.items():
            if column not in chunk_columns:
                db.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {column} {declaration}")
        db.execute("CREATE INDEX IF NOT EXISTS documents_source_path_idx ON documents(source_path)")
        db.execute("CREATE INDEX IF NOT EXISTS knowledge_assets_document_idx ON knowledge_assets(document_id,asset_index)")
        db.execute("CREATE INDEX IF NOT EXISTS knowledge_jobs_created_idx ON knowledge_jobs(created_at DESC)")
        db.execute(
            """
            UPDATE chat_sessions
            SET title = substr((
                SELECT content FROM messages
                WHERE messages.session_id = chat_sessions.id AND role = 'user'
                ORDER BY created_at, rowid LIMIT 1
            ), 1, 36)
            WHERE title IS NULL OR trim(title) = ''
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS sessions_updated_idx ON chat_sessions(updated_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS sessions_lifecycle_idx ON chat_sessions(deleted_at,archived_at,updated_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS sessions_management_idx ON chat_sessions(management_status,assignee,updated_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS audit_created_idx ON audit_logs(created_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS notes_session_idx ON session_notes(session_id,created_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS messages_session_created_idx ON messages(session_id,created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS runs_session_started_idx ON agent_runs(session_id,started_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS runs_status_started_idx ON agent_runs(status,started_at DESC)")
        db.execute(
            """
            DELETE FROM chat_sessions
            WHERE NOT EXISTS (SELECT 1 FROM messages WHERE messages.session_id=chat_sessions.id)
              AND NOT EXISTS (SELECT 1 FROM agent_runs WHERE agent_runs.session_id=chat_sessions.id)
              AND NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.session_id=chat_sessions.id)
              AND NOT EXISTS (SELECT 1 FROM session_notes WHERE session_notes.session_id=chat_sessions.id)
            """
        )
        defaults = {"deepseek": "deepseek-chat", "openai": "gpt-5-mini"}
        for provider, model in defaults.items():
            db.execute(
                "INSERT OR IGNORE INTO provider_configs(provider, model, updated_at) VALUES (?, ?, ?)",
                (provider, model, utc_now()),
            )
        db.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES ('active_provider', 'deepseek')")
        if not db.execute("SELECT 1 FROM prompt_versions LIMIT 1").fetchone():
            import hashlib
            import uuid

            now = utc_now()
            db.execute(
                "INSERT INTO prompt_versions(id,version,content,status,change_note,checksum,created_at,published_at) VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), 1, DEFAULT_BUSINESS_PROMPT, "published", "从内置客服规则初始化", hashlib.sha256(DEFAULT_BUSINESS_PROMPT.encode("utf-8")).hexdigest(), now, now),
            )


def recover_interrupted_runs() -> int:
    """Close runs left behind by an unclean shutdown before accepting traffic."""
    now = utc_now()
    with connect() as db:
        rows = db.execute("SELECT id,session_id FROM agent_runs WHERE status='running'").fetchall()
        if not rows:
            return 0
        db.execute(
            "UPDATE agent_runs SET status='interrupted',error_code='service_restarted',finished_at=? WHERE status='running'",
            (now,),
        )
        db.execute(
            "UPDATE agent_run_tools SET status='interrupted',finished_at=? WHERE finished_at IS NULL AND run_id IN (SELECT id FROM agent_runs WHERE error_code='service_restarted' AND finished_at=?)",
            (now, now),
        )
        session_ids = [row["session_id"] for row in rows]
        placeholders = ",".join("?" for _ in session_ids)
        db.execute(
            f"UPDATE chat_sessions SET status='idle',updated_at=? WHERE id IN ({placeholders})",
            (now, *session_ids),
        )
    return len(rows)
