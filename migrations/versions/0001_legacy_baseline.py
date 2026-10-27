"""PostgreSQL baseline preserving all legacy business tables."""

from alembic import op
from app.persistence.schema_v1 import metadata

revision = "0001_legacy_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Immutable schema snapshot, not mutable runtime application models.
    metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Destructive baseline downgrade is disabled; restore into an isolated database")
