"""Tenant-safe contacts, internal notes and attributed administrative audit."""

import sqlalchemy as sa
from alembic import op
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID

revision = "0005_admin_tenants"
down_revision = "0004_chat_tenants"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_chat_sessions_tenant_id_owner", "chat_sessions", ["tenant_id", "id", "owner_id"])
    for name, actor in (("contacts", "owner_id"), ("session_notes", "author_id"), ("audit_logs", "actor_id")):
        op.add_column(name, sa.Column("tenant_id", sa.Uuid(), nullable=False, server_default=LEGACY_TENANT_ID))
        op.add_column(name, sa.Column(actor, sa.Uuid(), nullable=True))
        if name in ("contacts", "session_notes"):
            # Inherit an already explicit parent ownership, never infer it from
            # phone/email. All legacy parent sessions remain quarantined.
            ownership = ", owner_id = s.owner_id" if name == "contacts" else ""
            op.execute(f"UPDATE {name} SET tenant_id = s.tenant_id{ownership} FROM chat_sessions s WHERE {name}.session_id = s.id")
        op.create_foreign_key(f"fk_{name}_tenant", name, "identity_tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key(f"fk_{name}_tenant_actor", name, "identity_memberships", ["tenant_id", actor], ["tenant_id", "user_id"], ondelete="RESTRICT")
        # Historical notes have no authenticated author; preserve NULL rather
        # than attribute them to the customer or invent an administrator.
        active_actor = "true" if name == "session_notes" else f"{actor} IS NOT NULL"
        op.create_check_constraint(f"{name}_actor_scope", name,
            f"(tenant_id = '{LEGACY_TENANT_ID}' AND {actor} IS NULL) OR (tenant_id <> '{LEGACY_TENANT_ID}' AND {active_actor})")
        op.create_index(f"ix_{name}_tenant_created", name, ["tenant_id", "created_at"])
    op.create_foreign_key("fk_contacts_tenant_session", "contacts", "chat_sessions", ["tenant_id", "session_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_contacts_tenant_session_owner", "contacts", "chat_sessions", ["tenant_id", "session_id", "owner_id"], ["tenant_id", "id", "owner_id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_session_notes_tenant_session", "session_notes", "chat_sessions", ["tenant_id", "session_id"], ["tenant_id", "id"], ondelete="CASCADE")


def downgrade():
    raise RuntimeError("Administrative ownership downgrade is disabled")
