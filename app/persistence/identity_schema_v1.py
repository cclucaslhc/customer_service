"""Immutable identity schema for migration 0003; no default accounts."""

import sqlalchemy as sa

metadata = sa.MetaData()
tenants = sa.Table("identity_tenants", metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
)
users = sa.Table("identity_users", metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("username", sa.String(64), nullable=False),
    sa.Column("password_hash", sa.Text(), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("auth_version > 0", name="identity_auth_version_positive"),
    sa.UniqueConstraint("username", name="uq_identity_users_username"),
)
memberships = sa.Table("identity_memberships", metadata,
    sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("identity_tenants.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("user_id", sa.Uuid(), sa.ForeignKey("identity_users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.CheckConstraint("role IN ('customer', 'support', 'admin')", name="identity_membership_role"),
)
sessions = sa.Table("identity_sessions", metadata,
    sa.Column("token_hash", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.Uuid(), nullable=False),
    sa.Column("tenant_id", sa.Uuid(), nullable=False),
    sa.Column("auth_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["identity_memberships.tenant_id", "identity_memberships.user_id"], ondelete="CASCADE"),
    sa.CheckConstraint("expires_at > created_at", name="identity_session_expiry"),
)
sa.Index("ix_identity_sessions_user_id", sessions.c.user_id)
sa.Index("ix_identity_sessions_expires_at", sessions.c.expires_at)
events = sa.Table("identity_events", metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("user_id", sa.Uuid(), nullable=False),
    sa.Column("tenant_id", sa.Uuid(), nullable=True),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
