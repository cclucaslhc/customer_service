"""Explicit encrypted tenant object storage settings."""
import sqlalchemy as sa
from alembic import op
from app.persistence.storage_config_v1 import add_storage_configs
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.identity_schema_v1 import metadata as identity

revision = '0016_storage_configs'
down_revision = '0015_knowledge_inputs'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    identity.tables['identity_tenants'].to_metadata(meta)
    add_storage_configs(meta)
    meta.tables['storage_configs'].create(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError('Storage configuration downgrade is disabled: referenced private objects need explicit credentials')
