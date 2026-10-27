"""Restored databases cannot start services or consume old queue commands."""
import sqlalchemy as sa
from alembic import op
from app.persistence.recovery_v1 import add_recovery_state
from app.persistence.schema_v1 import metadata as baseline

revision = '0014_recovery_hold'
down_revision = '0013_knowledge_queue'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    add_recovery_state(meta)
    meta.tables['recovery_state'].create(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError('Recovery hold downgrade is disabled: restored execution must remain fenced')
