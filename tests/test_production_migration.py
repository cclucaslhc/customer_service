"""Synthetic snapshots only; no app.main, SecretStore or real records."""

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
pytest.importorskip("sqlalchemy", reason="Install the production extra to verify PostgreSQL tooling")
pytest.importorskip("alembic", reason="Install the production extra to verify migrations")
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

import app.database as legacy
from app.persistence.engine import build_engine
from app.persistence.legacy_import import ImportValidationError, import_snapshot
from app.persistence.schema_v1 import LEGACY_TABLES, metadata


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    source = tmp_path / "snapshot.sqlite"
    monkeypatch.setattr(legacy, "DATABASE_PATH", source)
    monkeypatch.setattr(legacy, "ensure_directories", lambda: None)
    legacy.init_database()
    session_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with legacy.connect() as db:
        db.execute("INSERT INTO chat_sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (session_id, "Synthetic", now, now))
        db.execute("INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), session_id, "user", "Synthetic message", now))
        db.execute("INSERT INTO documents(id,display_name,stored_name,extension,size,sha256,status,created_at) VALUES(?,?,?,?,?,?,?,?)", (doc_id, "fixture", "fixture.txt", ".txt", 12, "a" * 64, "ready", now))
        db.execute("INSERT INTO knowledge_chunks(id,document_id,chunk_index,content,tokenized) VALUES(?,?,?,?,?)", (42, doc_id, 0, "synthetic", "synthetic"))
    # Close WAL through an explicit backup to create a standalone source.
    backup = tmp_path / "export.sqlite"
    with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(backup)) as destination:
        db.backup(destination)
        destination.execute("PRAGMA journal_mode=DELETE")
    return backup


@pytest.fixture
def target():
    engine = sa.create_engine("sqlite://", hide_parameters=True)
    @sa.event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_baseline_covers_all_legacy_business_columns(snapshot):
    with closing(sqlite3.connect(snapshot)) as source:
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {t.name for t in LEGACY_TABLES} == tables - {"sqlite_sequence", "knowledge_fts", "knowledge_fts_data", "knowledge_fts_idx", "knowledge_fts_docsize", "knowledge_fts_config"}
        for table in LEGACY_TABLES:
            assert set(table.c.keys()) == {row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')}
            ddl = str(sa.schema.CreateTable(table).compile(dialect=postgresql.dialect()))
            assert "CREATE TABLE" in ddl
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["0037_rag_phase_clock"]


def test_current_table_constraint_names_do_not_collide():
    from app.persistence.schema import metadata as current
    for table in current.tables.values():
        names=[constraint.name for constraint in table.constraints if constraint.name is not None]
        assert len(names)==len(set(names)),table.name


def test_dry_run_and_atomic_import_and_replay(snapshot, target):
    report = import_snapshot(snapshot, dry_run=True)
    assert report["status"] == "validated"
    assert report["tables"]["knowledge_chunks"]["count"] == 1
    imported = import_snapshot(snapshot, target)
    assert imported["status"] == "imported"
    assert imported["tables"] == report["tables"]
    assert import_snapshot(snapshot, target)["status"] == "already_imported"
    with target.begin() as connection:
        connection.execute(metadata.tables["messages"].update().values(content="modified"))
    with pytest.raises(ImportValidationError, match="changed"):
        import_snapshot(snapshot, target)


@pytest.mark.parametrize("column,value", [("id", "bad-uuid"), ("created_at", "2026-10-01"), ("citations_json", '{"wrong":"shape"}'), ("citations_json", '[NaN]')])
def test_invalid_source_fails_without_data_output(snapshot, target, column, value):
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute(f'UPDATE messages SET "{column}"=?', (value,))
    with pytest.raises(ImportValidationError, match="Invalid typed record") as error:
        import_snapshot(snapshot, target)
    assert value not in str(error.value)
    with target.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(metadata.tables["chat_sessions"])).scalar() == 0


def test_fk_and_constraint_failures_do_not_partially_import(snapshot, target):
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("UPDATE messages SET session_id=?", (str(uuid.uuid4()),))
    with pytest.raises(ImportValidationError, match="foreign key"):
        import_snapshot(snapshot, target)
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("UPDATE messages SET session_id=(SELECT id FROM chat_sessions LIMIT 1)")
        source.execute("UPDATE documents SET size=-1")
    with pytest.raises(sa.exc.IntegrityError):
        import_snapshot(snapshot, target)
    with target.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(metadata.tables["provider_configs"])).scalar() == 0


def test_live_paths_and_unbounded_driver_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_engine("sqlite://")
    for url in (
        "postgresql+psycopg://app@example.com/service",
        "postgresql+psycopg://app@example.com/service?sslmode=prefer",
        "postgresql+psycopg://app@example.com/service?sslmode=require",
        "postgresql+psycopg://app@example.com/service?sslmode=verify-ca",
        "postgresql+psycopg://app@localhost.evil/service?sslmode=disable",
        "postgresql+psycopg://app@localhost/service?host=example.com",
        "postgresql+psycopg://app@localhost/service?hostaddr=192.0.2.1",
        "postgresql+psycopg://app@localhost/service?service=remote",
        "postgresql+psycopg://app@/service",
    ):
        with pytest.raises(ValueError, match="sslmode=verify-full"):
            build_engine(url)
    for url in (
        "postgresql+psycopg://app@example.com/service?sslmode=verify-full",
        "postgresql+psycopg://app@127.0.0.1/service",
        "postgresql+psycopg://app@[::1]/service",
    ):
        engine = build_engine(url, pool_size=1)
        engine.dispose()
    unsafe = tmp_path / "secrets"
    unsafe.mkdir()
    file = unsafe / "snapshot.sqlite"
    file.touch()
    with pytest.raises(ImportValidationError, match="secret paths"):
        import_snapshot(file, dry_run=True)


def test_loopback_database_destination_is_pinned_not_inherited(monkeypatch):
    import app.persistence.engine as engine_module

    create_engine = engine_module.sa.create_engine
    seen = []

    def capture(*args, **kwargs):
        seen.append(kwargs['connect_args'].copy())
        return create_engine(*args, **kwargs)

    monkeypatch.setattr(engine_module.sa, 'create_engine', capture)
    for host, address in (('localhost', '127.0.0.1'), ('127.0.0.1', '127.0.0.1'), ('[::1]', '::1')):
        engine = build_engine(f'postgresql+psycopg://app@{host}/service')
        engine.dispose()
        assert seen[-1]['hostaddr'] == address


def test_remote_postgresql_defaults_to_runtime_role_gate_without_connecting():
    from app.persistence.bundle import PostgreSQLRepositories

    remote = PostgreSQLRepositories.from_url(
        'postgresql+psycopg://app@example.com/service?sslmode=verify-full', pool_size=1)
    try:
        assert remote.strict_role is True
    finally:
        remote.close()
    with pytest.raises(ValueError, match='cannot be disabled'):
        PostgreSQLRepositories.from_url(
            'postgresql+psycopg://app@example.com/service?sslmode=verify-full',
            pool_size=1, strict_role=False)
    override = PostgreSQLRepositories.from_url(
        'postgresql+psycopg://app@localhost/service?host=example.com&sslmode=verify-full', pool_size=1)
    try:
        assert override.strict_role is True
    finally:
        override.close()
    local = PostgreSQLRepositories.from_url('postgresql+psycopg://app@[::1]/service', pool_size=1)
    try:
        assert local.strict_role is False
    finally:
        local.close()


def test_nonempty_target_and_schema_drift_refused(snapshot, target):
    with target.begin() as connection:
        connection.execute(metadata.tables["app_settings"].insert().values(key="existing", value="keep"))
    with pytest.raises(ImportValidationError, match="must be empty"):
        import_snapshot(snapshot, target)
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("ALTER TABLE messages ADD COLUMN unsupported TEXT")
    with pytest.raises(ImportValidationError, match="Schema differs"):
        import_snapshot(snapshot, dry_run=True)


def test_binary_key_and_boolean_round_trip(snapshot, target):
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(snapshot)) as source, source:
        # Synthetic bytes only; no decryption or external credential access.
        source.execute("UPDATE provider_configs SET encrypted_key=?,nonce=? WHERE provider='deepseek'", (b"synthetic-ciphertext", b"synthetic-iv"))
        source.execute("INSERT INTO knowledge_jobs(id,source_path,recursive,status,created_at) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), "synthetic-path", 0, "pending", now))
    import_snapshot(snapshot, target)
    with target.connect() as connection:
        assert connection.execute(sa.select(metadata.tables["knowledge_jobs"].c.recursive)).scalar_one() is False
        assert connection.execute(sa.select(metadata.tables["provider_configs"].c.encrypted_key).where(metadata.tables["provider_configs"].c.provider == "deepseek")).scalar_one() == b"synthetic-ciphertext"


def test_offline_alembic_produces_postgresql_ddl(monkeypatch):
    from io import StringIO
    from alembic import command
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql+psycopg://offline@127.0.0.1/isolated")
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    ddl = output.getvalue()
    for table in LEGACY_TABLES:
        assert f"CREATE TABLE {table.name}" in ddl
    assert "JSONB" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl
    assert "BYTEA" in ddl
    assert "uq_messages_session_sequence" in ddl
    assert "message_sequence_positive" in ddl


def test_snapshot_fingerprint_detects_order_only_changes(snapshot):
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("INSERT INTO messages(id,session_id,role,content,created_at) "
                       "SELECT ?,session_id,'assistant','Synthetic answer',created_at FROM messages", (str(uuid.uuid4()),))
    before = import_snapshot(snapshot, dry_run=True)
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("UPDATE messages SET rowid=100 WHERE role='user'")
    after = import_snapshot(snapshot, dry_run=True)
    assert before["tables"] == after["tables"]
    assert before["message_order"] != after["message_order"]
    assert before["snapshot_sha256"] != after["snapshot_sha256"]
