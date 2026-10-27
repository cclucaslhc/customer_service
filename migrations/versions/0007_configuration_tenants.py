"""Tenant-local model settings and immutable prompt versions."""

import sqlalchemy as sa
from alembic import op
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID

revision = "0007_configuration_tenants"
down_revision = "0006_knowledge_tenants"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("provider_configs", "app_settings", "prompt_versions"):
        op.add_column(name, sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
        op.create_foreign_key(f"fk_{name}_tenant", name, "identity_tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
    for name, key in (("provider_configs", "provider"), ("app_settings", "key")):
        op.drop_constraint(f"pk_{name}", name, type_="primary")
        op.create_primary_key(f"pk_{name}", name, ["tenant_id", key])
    op.add_column("provider_configs", sa.Column("key_aad_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("provider_tenant_aad", "provider_configs",
        f"key_aad_version IN (0,1) AND (tenant_id = '{LEGACY_TENANT_ID}' OR encrypted_key IS NULL OR key_aad_version = 1)")
    op.drop_constraint("uq_prompt_versions_version", "prompt_versions", type_="unique")
    op.create_unique_constraint("uq_prompt_versions_tenant_version", "prompt_versions", ["tenant_id", "version"])
    op.create_unique_constraint("uq_prompt_versions_tenant_id", "prompt_versions", ["tenant_id", "id"])
    op.create_index("uq_prompt_versions_tenant_published", "prompt_versions", ["tenant_id"], unique=True, postgresql_where=sa.text("status = 'published'"))
    op.create_foreign_key("fk_agent_runs_tenant_prompt", "agent_runs", "prompt_versions", ["tenant_id", "prompt_version_id"], ["tenant_id", "id"], ondelete="RESTRICT")


def downgrade():
    raise RuntimeError("Configuration downgrade is disabled; tenant keys and versions may overlap")
