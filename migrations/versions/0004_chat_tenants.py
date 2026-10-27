"""Quarantine old chats and enforce tenant-consistent chat relationships."""

import sqlalchemy as sa
from alembic import op
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID, CHAT_TENANT_TABLES

revision = "0004_chat_tenants"
down_revision = "0003_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("INSERT INTO identity_tenants(id,name,active) VALUES "
                      f"('{LEGACY_TENANT_ID}', 'Restricted legacy data', false)"))
    for name in CHAT_TENANT_TABLES:
        op.add_column(name, sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
    op.add_column("chat_sessions", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_chat_sessions_tenant", "chat_sessions", "identity_tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_chat_sessions_tenant_owner", "chat_sessions", "identity_memberships", ["tenant_id", "owner_id"], ["tenant_id", "user_id"], ondelete="RESTRICT")
    op.create_check_constraint("chat_session_owner_scope", "chat_sessions",
        f"(tenant_id = '{LEGACY_TENANT_ID}' AND owner_id IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND owner_id IS NOT NULL)")
    for name in ("chat_sessions", "agent_runs"):
        op.create_unique_constraint(f"uq_{name}_tenant_id", name, ["tenant_id", "id"])
    for name in ("messages", "agent_runs"):
        op.create_foreign_key(f"fk_{name}_tenant_session", name, "chat_sessions", ["tenant_id", "session_id"], ["tenant_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_agent_run_tools_tenant_run", "agent_run_tools", "agent_runs", ["tenant_id", "run_id"], ["tenant_id", "id"], ondelete="CASCADE")
    op.create_index("ix_chat_sessions_tenant_owner_updated", "chat_sessions", ["tenant_id", "owner_id", "updated_at"])


def downgrade():
    raise RuntimeError("Tenant isolation downgrade is disabled; ownership must not be discarded")
