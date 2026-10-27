"""Tenant-local knowledge and settings, with consistent document children."""

import sqlalchemy as sa
from alembic import op
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID

revision = "0006_knowledge_tenants"
down_revision = "0005_admin_tenants"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("documents", "knowledge_chunks", "knowledge_assets", "knowledge_jobs"):
        op.add_column(name, sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
    for name, actor in (("documents", "created_by"), ("knowledge_jobs", "requested_by")):
        op.add_column(name, sa.Column(actor, sa.Uuid(), nullable=True))
        op.create_foreign_key(f"fk_{name}_tenant", name, "identity_tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key(f"fk_{name}_tenant_actor", name, "identity_memberships", ["tenant_id", actor], ["tenant_id", "user_id"], ondelete="RESTRICT")
        op.create_check_constraint(f"{name}_actor_scope", name,
            f"(tenant_id = '{LEGACY_TENANT_ID}' AND {actor} IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND {actor} IS NOT NULL)")
        op.create_index(f"ix_{name}_tenant_created", name, ["tenant_id", "created_at"])
    op.create_unique_constraint("uq_documents_tenant_id", "documents", ["tenant_id", "id"])
    op.drop_constraint("uq_documents_sha256", "documents", type_="unique")
    op.create_unique_constraint("uq_documents_tenant_sha256", "documents", ["tenant_id", "sha256"])
    for name in ("knowledge_chunks", "knowledge_assets"):
        op.create_foreign_key(f"fk_{name}_tenant_document", name, "documents", ["tenant_id", "document_id"], ["tenant_id", "id"], ondelete="CASCADE")
    op.create_table("knowledge_settings",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("identity_tenants.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True), sa.Column("value", sa.Text(), nullable=False))
    op.execute(f"INSERT INTO knowledge_settings(tenant_id,key,value) SELECT '{LEGACY_TENANT_ID}',key,value FROM app_settings WHERE key LIKE 'knowledge\\_%' ESCAPE '\\'")


def downgrade():
    raise RuntimeError("Knowledge tenant downgrade is disabled; tenant-local hashes may overlap")
