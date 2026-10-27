"""Validate and atomically import an explicitly supplied, isolated SQLite snapshot.

Does not open the application's DATABASE_PATH or SecretStore. No model calls.
Reports contain counts/hashes only, never record values or source paths.
"""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from app.persistence.schema_v1 import LEGACY_TABLES, migration_batches
from app.persistence.schema import metadata as current_metadata, SCHEMA_REVISION
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID, CHAT_TENANT_TABLES


class ImportValidationError(ValueError):
    pass


def safe_snapshot(path: Path) -> Path:
    path = path.resolve(strict=True)
    workspace_data = Path(__file__).resolve().parents[3] / "data"
    if path == workspace_data or workspace_data in path.parents or "secrets" in {part.casefold() for part in path.parts} or path.name.casefold().startswith(".env"):
        raise ImportValidationError("Live application data and secret paths cannot be migration sources")
    if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ImportValidationError("An isolated SQLite snapshot file is required")
    if Path(str(path) + "-wal").exists():
        raise ImportValidationError("Source has a WAL sidecar; use SQLite backup to produce a standalone snapshot")
    return path


@contextmanager
def source_connection(path: Path):
    selected = safe_snapshot(path)
    connection = sqlite3.connect(selected.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("migration_uuid", 1, lambda value: str(uuid.UUID(str(value))), deterministic=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def convert(column: sa.Column, value):
    if value is None:
        if not column.nullable:
            raise ImportValidationError("Required value is missing")
        return None
    if isinstance(column.type, sa.Uuid):
        return uuid.UUID(str(value))
    if isinstance(column.type, sa.DateTime):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ImportValidationError("Timestamp requires timezone")
        return parsed.astimezone(timezone.utc)
    if isinstance(column.type, sa.JSON):
        parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
        expected = list if column.name in {"tags_json", "citations_json", "warnings_json"} else dict
        if not isinstance(parsed, expected):
            raise ImportValidationError("Invalid JSON container")
        return parsed
    if isinstance(column.type, sa.Boolean):
        if value not in (0, 1):
            raise ImportValidationError("Invalid boolean")
        return bool(value)
    return value


def rows(source, table):
    names = {row["name"] for row in source.execute(f'PRAGMA table_info("{table.name}")')}
    if names != set(table.c.keys()):
        raise ImportValidationError(f"Schema differs for {table.name}; upgrade an isolated source snapshot first")
    order = ",".join(f'"{c.name}"' for c in table.primary_key.columns)
    for record in source.execute(f'SELECT * FROM "{table.name}" ORDER BY {order}'):
        try:
            yield {column.name: convert(column, record[column.name]) for column in table.columns}
        except (ValueError, TypeError, OverflowError):
            # Never expose the record, JSON parser payload or UUID in error text.
            raise ImportValidationError(f"Invalid typed record in {table.name}") from None


def canonical(value):
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, bytes):
        return {"binary_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)  # SQLite dialect round-trip for test destinations.
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def summarize(records):
    count = 0
    digest = hashlib.sha256()
    for record in records:
        encoded = json.dumps(canonical(dict(record)), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def ordered_messages(source):
    """SQLite's existing history semantics, including equal-timestamp rowid ties."""
    table = next(table for table in LEGACY_TABLES if table.name == "messages")
    previous, ordinal = None, 0
    for record in source.execute('SELECT * FROM messages ORDER BY migration_uuid(session_id), created_at, rowid'):
        sid = uuid.UUID(str(record["session_id"]))
        ordinal = ordinal + 1 if sid == previous else 1
        previous = sid
        yield {**{column.name: convert(column, record[column.name]) for column in table.columns}, "sequence": ordinal}


def message_order_summary(records):
    return summarize({name: row[name] for name in ("session_id", "id", "sequence")} for row in records)


def validate_source(source):
    if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ImportValidationError("SQLite integrity check failed")
    if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ImportValidationError("Source has foreign key violations")
    tables = {table.name: summarize(rows(source, table)) for table in LEGACY_TABLES}
    # This semantic snapshot fingerprint remains stable across SQLite file packing.
    order = message_order_summary(ordered_messages(source))
    fingerprint = hashlib.sha256(json.dumps({"tables": tables, "message_order": order}, sort_keys=True).encode()).hexdigest()
    return {"snapshot_sha256": fingerprint, "tables": tables, "message_order": order}


def target_summary(connection):
    return {
        table.name: summarize(connection.execute(sa.select(table).order_by(*table.primary_key.columns)).mappings())
        for table in LEGACY_TABLES
    }


def import_snapshot(path: Path, engine=None, *, dry_run=False):
    with source_connection(path) as source:
        report = validate_source(source)
        if dry_run:
            return {**report, "status": "validated"}
        if engine is None:
            raise ImportValidationError("An explicit destination is required")
        with engine.begin() as target:
            if target.dialect.name == "postgresql":
                target.exec_driver_sql("SELECT pg_advisory_xact_lock(746219852)")
                revision = target.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                if revision != SCHEMA_REVISION:
                    raise ImportValidationError("Importer requires current schema revision " + SCHEMA_REVISION)
            def verify_order():
                if target.dialect.name == "postgresql":
                    tenants = current_metadata.tables["identity_tenants"]
                    if target.execute(sa.select(tenants.c.active).where(tenants.c.id == uuid.UUID(LEGACY_TENANT_ID))).scalar_one() is not False:
                        raise ImportValidationError("Destination legacy quarantine must remain inactive")
                    settings = current_metadata.tables["knowledge_settings"]
                    expected_settings = summarize(dict(row) for row in source.execute(
                        "SELECT key,value FROM app_settings WHERE key GLOB 'knowledge_*' ORDER BY key"))
                    actual_settings = summarize(target.execute(sa.select(settings.c.key, settings.c.value)
                        .where(settings.c.tenant_id == uuid.UUID(LEGACY_TENANT_ID)).order_by(settings.c.key)).mappings())
                    if actual_settings != expected_settings:
                        raise ImportValidationError("Destination legacy knowledge settings verification failed")
                    providers = current_metadata.tables["provider_configs"]
                    if target.execute(sa.select(sa.exists(sa.select(providers).where(
                        providers.c.key_aad_version != 0)))).scalar_one():
                        raise ImportValidationError("Destination legacy credential format verification failed")
                    runs = current_metadata.tables['agent_runs']
                    if target.execute(sa.select(sa.exists(sa.select(runs.c.id).where(sa.or_(
                        runs.c.lease_owner.is_not(None), runs.c.lease_until.is_not(None),
                        runs.c.lease_fence != 0, runs.c.cancel_requested_at.is_not(None)))))).scalar_one():
                        raise ImportValidationError('Destination legacy run lease verification failed')
                    if target.execute(sa.select(sa.exists(sa.select(runs.c.id).where(sa.or_(
                        runs.c.execution_mode != 'direct', runs.c.event_sequence != 0,
                        runs.c.event_data_bytes != 0,runs.c.event_floor != 0,
                        runs.c.admission_started_at.is_not(None),runs.c.admission_until.is_not(None)))))).scalar_one():
                        raise ImportValidationError('Destination legacy durable run verification failed')
                    jobs = current_metadata.tables['knowledge_jobs']
                    if target.execute(sa.select(sa.exists(sa.select(jobs.c.id).where(sa.or_(
                        jobs.c.execution_mode != 'local',jobs.c.lease_owner.is_not(None),jobs.c.lease_until.is_not(None),jobs.c.lease_fence != 0))))).scalar_one():
                        raise ImportValidationError('Destination legacy knowledge execution verification failed')
                    for name in ('chat_run_queue', 'chat_run_events','chat_run_snapshots','knowledge_job_queue','knowledge_job_items','recovery_state','storage_configs','maintenance_state','backup_schedules','backup_jobs','model_usage_attempts','model_price_versions','model_price_quotes','model_cost_assessments','model_budget_policies','model_budget_reservations','model_route_budget_policies','model_circuit_state'):
                        if target.execute(sa.select(sa.func.count()).select_from(current_metadata.tables[name])).scalar_one():
                            raise ImportValidationError('Destination legacy durable ledger must be empty')
                    for name in (*CHAT_TENANT_TABLES, "contacts", "session_notes", "audit_logs", "documents", "knowledge_chunks", "knowledge_assets", "knowledge_jobs", "provider_configs", "app_settings", "prompt_versions"):
                        table = current_metadata.tables[name]
                        conditions = [table.c.tenant_id != uuid.UUID(LEGACY_TENANT_ID)]
                        for actor in ("owner_id", "author_id", "actor_id", "created_by", "requested_by"):
                            if actor in table.c:
                                conditions.append(table.c[actor].is_not(None))
                        if target.execute(sa.select(sa.exists(sa.select(table).where(sa.or_(*conditions))))).scalar_one():
                            raise ImportValidationError("Destination legacy quarantine verification failed")
                    messages = current_metadata.tables["messages"]
                    actual = message_order_summary(target.execute(sa.select(messages)
                        .order_by(messages.c.session_id, messages.c.sequence)).mappings())
                    if actual != report["message_order"]:
                        raise ImportValidationError("Destination message order verification failed")
            batch = target.execute(sa.select(migration_batches).where(
                migration_batches.c.snapshot_sha256 == report["snapshot_sha256"]
            )).mappings().first()
            if batch:
                if target_summary(target) != report["tables"]:
                    raise ImportValidationError("Previously imported data has changed; refusing a replay")
                verify_order()
                return {**report, "status": "already_imported"}
            if any(target.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() for table in LEGACY_TABLES):
                raise ImportValidationError("Destination business tables must be empty")
            for table in LEGACY_TABLES:
                buffer = []
                preserve_order = table.name == "messages" and target.dialect.name == "postgresql"
                insert_table = current_metadata.tables[table.name] if preserve_order else table
                for row in ordered_messages(source) if preserve_order else rows(source, table):
                    buffer.append(row)
                    if len(buffer) == 500:
                        target.execute(insert_table.insert(), buffer)
                        buffer = []
                if buffer:
                    target.execute(insert_table.insert(), buffer)
            if target.dialect.name == "postgresql":
                target.execute(sa.text("INSERT INTO knowledge_settings(tenant_id,key,value) "
                    "SELECT :tenant,key,value FROM app_settings WHERE key LIKE 'knowledge\\_%' ESCAPE '\\' "
                    "ON CONFLICT (tenant_id,key) DO NOTHING"), {"tenant": uuid.UUID(LEGACY_TENANT_ID)})
            if target_summary(target) != report["tables"]:
                raise ImportValidationError("Destination count/hash verification failed")
            verify_order()
            if target.dialect.name == "postgresql":
                for table in LEGACY_TABLES:
                    if isinstance(table.c.get("id").type if "id" in table.c else None, sa.Integer):
                        target.execute(sa.text(
                            "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                            f'COALESCE((SELECT MAX(id) FROM "{table.name}"), 1), '
                            f'EXISTS(SELECT 1 FROM "{table.name}"))'
                        ), {"table_name": table.name})
            target.execute(migration_batches.insert().values(
                snapshot_sha256=report["snapshot_sha256"], report_json=report,
                completed_at=datetime.now(timezone.utc),
            ))
        return {**report, "status": "imported"}
