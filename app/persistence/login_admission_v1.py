"""Immutable migration 0008: shared login budgets and bounded KDF leases."""

import sqlalchemy as sa

metadata = sa.MetaData()
buckets = sa.Table("identity_login_buckets", metadata,
    sa.Column("scope_hash", sa.String(64), primary_key=True),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("attempts > 0 AND attempts <= 60", name="login_bucket_attempts"),
)
sa.Index("ix_identity_login_buckets_expiry", buckets.c.expires_at)
slots = sa.Table("identity_login_slots", metadata,
    sa.Column("slot", sa.Integer(), primary_key=True),
    sa.Column("owner", sa.Uuid(), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("slot IN (1,2)", name="login_slot_capacity"),
    sa.CheckConstraint("(owner IS NULL) = (expires_at IS NULL)", name="login_slot_pair"),
)
