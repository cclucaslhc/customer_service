import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.settings import DATABASE_PATH, ensure_directories
from app.prompting import DEFAULT_BUSINESS_PROMPT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_directories()
    conn = sqlite3.connect(DATABASE_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
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
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                section TEXT,
                location TEXT,
                content TEXT NOT NULL,
                tokenized TEXT NOT NULL
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
