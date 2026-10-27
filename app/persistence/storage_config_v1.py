"""Immutable 0016 explicit tenant storage credentials, no environment defaults."""
import sqlalchemy as sa


def add_storage_configs(meta):
    sa.Table('storage_configs', meta,
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('identity_tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('target', sa.String(64), nullable=False),
        sa.Column('target_fingerprint', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False, server_default='active'),
        sa.Column('encrypted_credentials', sa.LargeBinary(), nullable=False),
        sa.Column('nonce', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('revision > 0', name='storage_revision_positive'),
        sa.CheckConstraint("state IN ('active','recovery_rebind')", name='storage_state'))
