"""Durable principals and revocable login sessions, without public registration."""

from alembic import op
from app.persistence.identity_schema_v1 import metadata

revision = "0003_identity"
down_revision = "0002_message_order"
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Identity downgrade is disabled; it would remove revocation state")
