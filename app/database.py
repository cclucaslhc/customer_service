import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.settings import DATABASE_PATH, ensure_directories


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
                status TEXT NOT NULL DEFAULT 'idle',
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
                citations_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                provider TEXT,
                model TEXT,
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
                created_at TEXT NOT NULL
            );
            """
        )
        defaults = {"deepseek": "deepseek-chat", "openai": "gpt-5-mini"}
        for provider, model in defaults.items():
            db.execute(
                "INSERT OR IGNORE INTO provider_configs(provider, model, updated_at) VALUES (?, ?, ?)",
                (provider, model, utc_now()),
            )
        db.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES ('active_provider', 'deepseek')")
