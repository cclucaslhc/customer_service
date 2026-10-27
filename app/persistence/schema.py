"""Current runtime schema, separate from the immutable migration baseline."""

import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.identity_schema_v1 import metadata as identity
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID, CHAT_TENANT_TABLES

SCHEMA_REVISION = "0037_rag_phase_clock"
metadata = sa.MetaData(naming_convention=baseline.naming_convention)
for table in baseline.tables.values():
    table.to_metadata(metadata)
messages = metadata.tables["messages"]
messages.append_column(sa.Column("sequence", sa.BigInteger(), nullable=False))
messages.append_constraint(sa.UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"))
messages.append_constraint(sa.CheckConstraint("sequence > 0", name="message_sequence_positive"))
for table in identity.tables.values():
    table.to_metadata(metadata)
from app.persistence.login_admission_v1 import metadata as login_admission
for table in login_admission.tables.values():
    table.to_metadata(metadata)
for name in CHAT_TENANT_TABLES:
    metadata.tables[name].append_column(sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
sessions = metadata.tables["chat_sessions"]
sessions.append_column(sa.Column("owner_id", sa.Uuid(), nullable=True))
sessions.append_constraint(sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], name="fk_chat_sessions_tenant", ondelete="RESTRICT"))
sessions.append_constraint(sa.ForeignKeyConstraint(["tenant_id", "owner_id"], ["identity_memberships.tenant_id", "identity_memberships.user_id"], name="fk_chat_sessions_tenant_owner", ondelete="RESTRICT"))
sessions.append_constraint(sa.CheckConstraint(
    f"(tenant_id = '{LEGACY_TENANT_ID}' AND owner_id IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND owner_id IS NOT NULL)", name="chat_session_owner_scope"))
for name in ("chat_sessions", "agent_runs"):
    table = metadata.tables[name]
    table.append_constraint(sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id"))
for name in ("messages", "agent_runs"):
    metadata.tables[name].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "session_id"], ["chat_sessions.tenant_id", "chat_sessions.id"], name=f"fk_{name}_tenant_session", ondelete="CASCADE"))
metadata.tables["agent_run_tools"].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["agent_runs.tenant_id", "agent_runs.id"], name="fk_agent_run_tools_tenant_run", ondelete="CASCADE"))
sa.Index("ix_chat_sessions_tenant_owner_updated", sessions.c.tenant_id, sessions.c.owner_id, sessions.c.updated_at)
for name, actor in (("contacts", "owner_id"), ("session_notes", "author_id"), ("audit_logs", "actor_id")):
    table = metadata.tables[name]
    table.append_column(sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
    table.append_column(sa.Column(actor, sa.Uuid(), nullable=True))
    table.append_constraint(sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], name=f"fk_{name}_tenant", ondelete="RESTRICT"))
    table.append_constraint(sa.ForeignKeyConstraint(["tenant_id", actor], ["identity_memberships.tenant_id", "identity_memberships.user_id"], name=f"fk_{name}_tenant_actor", ondelete="RESTRICT"))
    active_actor = "true" if name == "session_notes" else f"{actor} IS NOT NULL"
    table.append_constraint(sa.CheckConstraint(
        f"(tenant_id = '{LEGACY_TENANT_ID}' AND {actor} IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND {active_actor})", name=f"{name}_actor_scope"))
    sa.Index(f"ix_{name}_tenant_created", table.c.tenant_id, table.c.created_at)
metadata.tables["contacts"].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "session_id"], ["chat_sessions.tenant_id", "chat_sessions.id"], name="fk_contacts_tenant_session", ondelete="RESTRICT"))
sessions.append_constraint(sa.UniqueConstraint("tenant_id", "id", "owner_id", name="uq_chat_sessions_tenant_id_owner"))
metadata.tables["contacts"].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "session_id", "owner_id"], ["chat_sessions.tenant_id", "chat_sessions.id", "chat_sessions.owner_id"], name="fk_contacts_tenant_session_owner", ondelete="RESTRICT"))
metadata.tables["session_notes"].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "session_id"], ["chat_sessions.tenant_id", "chat_sessions.id"], name="fk_session_notes_tenant_session", ondelete="CASCADE"))
for name in ("documents", "knowledge_chunks", "knowledge_assets", "knowledge_jobs"):
    metadata.tables[name].append_column(sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
for name, actor in (("documents", "created_by"), ("knowledge_jobs", "requested_by")):
    table = metadata.tables[name]
    table.append_column(sa.Column(actor, sa.Uuid(), nullable=True))
    table.append_constraint(sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], name=f"fk_{name}_tenant", ondelete="RESTRICT"))
    table.append_constraint(sa.ForeignKeyConstraint(["tenant_id", actor], ["identity_memberships.tenant_id", "identity_memberships.user_id"], name=f"fk_{name}_tenant_actor", ondelete="RESTRICT"))
    table.append_constraint(sa.CheckConstraint(
        f"(tenant_id = '{LEGACY_TENANT_ID}' AND {actor} IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND {actor} IS NOT NULL)", name=f"{name}_actor_scope"))
    sa.Index(f"ix_{name}_tenant_created", table.c.tenant_id, table.c.created_at)
documents = metadata.tables["documents"]
for constraint in list(documents.constraints):
    if isinstance(constraint, sa.UniqueConstraint) and tuple(constraint.columns.keys()) == ("sha256",):
        documents.constraints.remove(constraint)
documents.append_constraint(sa.UniqueConstraint("tenant_id", "sha256", name="uq_documents_tenant_sha256"))
documents.append_constraint(sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id"))
for name in ("knowledge_chunks", "knowledge_assets"):
    metadata.tables[name].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"], name=f"fk_{name}_tenant_document", ondelete="CASCADE"))
sa.Table("knowledge_settings", metadata,
    sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("identity_tenants.id", ondelete="RESTRICT"), primary_key=True),
    sa.Column("key", sa.Text(), primary_key=True), sa.Column("value", sa.Text(), nullable=False))
for name in ("provider_configs", "app_settings", "prompt_versions"):
    table = metadata.tables[name]
    table.append_column(sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
    table.append_constraint(sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], name=f"fk_{name}_tenant", ondelete="RESTRICT"))
for name, key in (("provider_configs", "provider"), ("app_settings", "key")):
    table = metadata.tables[name]
    table.constraints.remove(table.primary_key)
    table.c.tenant_id.primary_key = True
    table.append_constraint(sa.PrimaryKeyConstraint("tenant_id", key, name=f"pk_{name}"))
providers = metadata.tables["provider_configs"]
providers.append_column(sa.Column("key_aad_version", sa.Integer(), nullable=False, server_default="0"))
providers.append_constraint(sa.CheckConstraint(
    f"key_aad_version IN (0,1) AND (tenant_id = '{LEGACY_TENANT_ID}' OR encrypted_key IS NULL OR key_aad_version = 1)", name="provider_tenant_aad"))
prompts = metadata.tables["prompt_versions"]
for constraint in list(prompts.constraints):
    if isinstance(constraint, sa.UniqueConstraint) and tuple(constraint.columns.keys()) == ("version",):
        prompts.constraints.remove(constraint)
prompts.append_constraint(sa.UniqueConstraint("tenant_id", "version", name="uq_prompt_versions_tenant_version"))
prompts.append_constraint(sa.UniqueConstraint("tenant_id", "id", name="uq_prompt_versions_tenant_id"))
sa.Index("uq_prompt_versions_tenant_published", prompts.c.tenant_id, unique=True, postgresql_where=sa.text("status = 'published'"))
metadata.tables["agent_runs"].append_constraint(sa.ForeignKeyConstraint(["tenant_id", "prompt_version_id"], ["prompt_versions.tenant_id", "prompt_versions.id"], name="fk_agent_runs_tenant_prompt", ondelete="RESTRICT"))
from app.persistence.run_lease_v1 import add_run_leases
add_run_leases(metadata)
from app.persistence.durable_chat_v1 import add_durable_chat
add_durable_chat(metadata)
from app.persistence.run_snapshot_v1 import add_run_snapshots
add_run_snapshots(metadata)
from app.persistence.chat_admission_v1 import add_chat_admission
add_chat_admission(metadata)
from app.persistence.knowledge_queue_v1 import add_knowledge_queue
add_knowledge_queue(metadata)
from app.persistence.recovery_v1 import add_recovery_state
add_recovery_state(metadata)
from app.persistence.knowledge_input_v1 import add_knowledge_inputs
add_knowledge_inputs(metadata)
from app.persistence.storage_config_v1 import add_storage_configs
add_storage_configs(metadata)
from app.persistence.maintenance_v1 import add_maintenance
add_maintenance(metadata)
from app.persistence.backup_schedule_v1 import add_backup_schedule
add_backup_schedule(metadata)
from app.persistence.trace_context_v1 import add_trace_context
add_trace_context(metadata)
from app.persistence.trace_context_v1 import add_knowledge_trace_context
add_knowledge_trace_context(metadata)
from app.persistence.model_usage_v1 import add_model_usage
add_model_usage(metadata)
from app.persistence.model_cost_v1 import add_model_cost
add_model_cost(metadata)
from app.persistence.model_budget_v1 import add_model_budget
add_model_budget(metadata)
from app.persistence.model_route_budget_v1 import add_model_route_budget
add_model_route_budget(metadata)
from app.persistence.model_circuit_v1 import add_model_circuit
add_model_circuit(metadata)
from app.persistence.rag_metadata_v1 import add_rag_metadata
add_rag_metadata(metadata)
from app.persistence.rag_chunk_v1 import add_rag_chunk_versions
add_rag_chunk_versions(metadata)
from app.persistence.rag_vector_v1 import add_rag_vectors
add_rag_vectors(metadata)
from app.persistence.knowledge_admission_v1 import add_knowledge_admission
add_knowledge_admission(metadata)
from app.persistence.knowledge_action_v1 import add_knowledge_actions
add_knowledge_actions(metadata)
from app.persistence.rag_release_v1 import add_rag_release_intents
add_rag_release_intents(metadata)
from app.persistence.rag_rollout_v1 import add_rag_rollouts
add_rag_rollouts(metadata)
metadata.tables['rag_rollouts'].append_column(sa.Column('step_started_at',sa.DateTime(timezone=True)))
metadata.tables['rag_rollouts'].append_constraint(sa.CheckConstraint(
    "(state<>'running' OR step_started_at IS NOT NULL) AND "
    "(step_started_at IS NULL OR (step_started_at>=created_at AND step_started_at<expires_at))",
    name='rag_rollout_phase_clock'))
from sqlalchemy.dialects.postgresql import JSONB
metadata.tables['agent_runs'].append_column(sa.Column('rag_route',sa.JSON().with_variant(JSONB(),'postgresql')))
metadata.tables['agent_runs'].append_constraint(sa.CheckConstraint(
    "rag_route IS NULL OR (jsonb_typeof(rag_route)='object' AND octet_length(rag_route::text)<=4096)",
    name='rag_route_shape'))
sa.Index('ix_agent_runs_rag_epoch',metadata.tables['agent_runs'].c.tenant_id,
    sa.text("(rag_route->>'rollout_id')"),sa.text("(rag_route->>'route_version')"),
    sa.text("(rag_route->>'index_id')"),postgresql_where=sa.text('rag_route IS NOT NULL'))
