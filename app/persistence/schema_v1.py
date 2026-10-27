"""Immutable schema for migration 0001. Never change this module after release.

Future structure changes belong in new Alembic revisions, not create_all at startup.
The first revision preserves the legacy records; tenant isolation is a later revision.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


metadata = sa.MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def text(name, *, nullable=True, default=None):
    return sa.Column(name, sa.Text(), nullable=nullable, server_default=default)


def uuid_pk():
    return sa.Column("id", sa.Uuid(), primary_key=True)


def fk(name, target, *, nullable=False, ondelete="CASCADE"):
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def date(name, *, nullable=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def integer(name, *, default="0", nullable=False):
    return sa.Column(name, sa.Integer(), nullable=nullable, server_default=default)


def json_column(name, default="{}"):
    return sa.Column(name, JSON_TYPE, nullable=False, server_default=default)


def counter_pk():
    return sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True)


provider_configs = sa.Table("provider_configs", metadata,
    sa.Column("provider", sa.Text(), primary_key=True), text("model", nullable=False),
    sa.Column("encrypted_key", sa.LargeBinary()), sa.Column("nonce", sa.LargeBinary()),
    date("updated_at", nullable=False), text("last_test_status"), text("last_test_message"), date("last_test_at"),
    sa.CheckConstraint("(encrypted_key IS NULL) = (nonce IS NULL)", name="provider_key_pair"),
)
app_settings = sa.Table("app_settings", metadata,
    sa.Column("key", sa.Text(), primary_key=True), text("value", nullable=False),
)
chat_sessions = sa.Table("chat_sessions", metadata,
    uuid_pk(), text("title"), text("status", nullable=False, default="idle"),
    text("management_status", nullable=False, default="open"), text("assignee"), json_column("tags_json", "[]"),
    date("archived_at"), date("deleted_at"), date("created_at", nullable=False), date("updated_at", nullable=False),
)
messages = sa.Table("messages", metadata,
    uuid_pk(), fk("session_id", "chat_sessions.id"), text("role", nullable=False), text("content", nullable=False),
    text("status", nullable=False, default="complete"), text("provider"), text("model"), text("feedback"),
    json_column("citations_json", "[]"), date("created_at", nullable=False),
    sa.CheckConstraint("feedback IN ('up', 'down')", name="message_feedback"),
)
prompt_versions = sa.Table("prompt_versions", metadata,
    uuid_pk(), sa.Column("version", sa.Integer(), nullable=False, unique=True), text("content", nullable=False),
    text("status", nullable=False), text("change_note", nullable=False, default=""), text("checksum", nullable=False),
    date("created_at", nullable=False), date("published_at"),
)
agent_runs = sa.Table("agent_runs", metadata,
    uuid_pk(), fk("session_id", "chat_sessions.id"), text("status", nullable=False), text("provider"), text("model"),
    fk("prompt_version_id", "prompt_versions.id", nullable=True, ondelete="RESTRICT"), text("error_code"),
    date("started_at", nullable=False), date("finished_at"),
)
documents = sa.Table("documents", metadata,
    uuid_pk(), text("display_name", nullable=False), text("stored_name", nullable=False), text("extension", nullable=False),
    integer("size", default=None), sa.Column("sha256", sa.Text(), nullable=False, unique=True),
    text("status", nullable=False), text("error_message"), integer("chunk_count"), text("detected_type"), text("source_path"),
    sa.Column("source_mtime", sa.Float()), text("parser_version", nullable=False, default="1.0"),
    text("chunker_version", nullable=False, default="1.0"), date("indexed_at"), integer("version", default="1"),
    json_column("warnings_json", "[]"), json_column("metadata_json"), date("created_at", nullable=False),
    sa.CheckConstraint("size >= 0 AND chunk_count >= 0 AND version > 0", name="document_counts"),
)
knowledge_chunks = sa.Table("knowledge_chunks", metadata,
    counter_pk(), fk("document_id", "documents.id"), integer("chunk_index", default=None), text("section"), text("location"),
    text("content", nullable=False), text("tokenized", nullable=False), text("block_type", nullable=False, default="paragraph"),
    json_column("metadata_json"), integer("char_count"),
    sa.UniqueConstraint("document_id", "chunk_index"),
)
knowledge_assets = sa.Table("knowledge_assets", metadata,
    uuid_pk(), fk("document_id", "documents.id"), integer("asset_index", default=None), text("stored_name", nullable=False),
    text("media_type", nullable=False), integer("size", default=None), integer("width", default=None), integer("height", default=None),
    text("location", nullable=False), text("alt_text", nullable=False), text("sha256", nullable=False),
    json_column("metadata_json"), date("created_at", nullable=False), sa.UniqueConstraint("document_id", "asset_index"),
)
knowledge_jobs = sa.Table("knowledge_jobs", metadata,
    uuid_pk(), text("source_path", nullable=False), sa.Column("recursive", sa.Boolean(), nullable=False, server_default=sa.true()),
    text("status", nullable=False), text("stage", nullable=False, default="pending"),
    *(integer(name) for name in ("total_count", "processed_count", "imported_count", "skipped_count", "failed_count")),
    text("current_file"), json_column("result_json"), text("error_message"), date("created_at", nullable=False),
    date("started_at"), date("finished_at"),
)
contacts = sa.Table("contacts", metadata,
    uuid_pk(), fk("session_id", "chat_sessions.id", nullable=True, ondelete="SET NULL"), text("name", nullable=False),
    text("phone"), text("email"), text("summary", nullable=False), text("status", nullable=False, default="new"),
    text("note"), date("created_at", nullable=False),
)
session_notes = sa.Table("session_notes", metadata,
    uuid_pk(), fk("session_id", "chat_sessions.id"), text("content", nullable=False), date("created_at", nullable=False),
)
agent_run_tools = sa.Table("agent_run_tools", metadata,
    counter_pk(), fk("run_id", "agent_runs.id"), text("tool_name", nullable=False), text("status", nullable=False),
    date("started_at", nullable=False), date("finished_at"),
)
audit_logs = sa.Table("audit_logs", metadata,
    uuid_pk(), text("action", nullable=False), text("entity_type", nullable=False), text("entity_id"), text("summary", nullable=False),
    json_column("metadata_json"), text("actor", nullable=False, default="local_admin"), date("created_at", nullable=False),
)

for table, columns in (
    (chat_sessions, ("updated_at",)), (chat_sessions, ("deleted_at", "archived_at", "updated_at")),
    (chat_sessions, ("management_status", "assignee", "updated_at")), (messages, ("session_id", "created_at")),
    (agent_runs, ("session_id", "started_at")), (agent_runs, ("status", "started_at")),
    (documents, ("source_path",)), (knowledge_jobs, ("created_at",)), (audit_logs, ("created_at",)),
    (session_notes, ("session_id", "created_at")), (agent_run_tools, ("run_id", "started_at")),
):
    sa.Index(f"ix_{table.name}_{'_'.join(columns)}", *(table.c[name] for name in columns))

# Tracking table is excluded from legacy business data.
migration_batches = sa.Table("migration_batches", metadata,
    sa.Column("snapshot_sha256", sa.String(64), primary_key=True),
    json_column("report_json"), date("completed_at", nullable=False),
)
LEGACY_TABLES = tuple(t for t in metadata.sorted_tables if t.name != "migration_batches")
