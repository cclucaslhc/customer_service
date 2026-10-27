"""Durable cross-instance login limits, without public registration."""

from alembic import op
from app.persistence.login_admission_v1 import metadata, slots

revision = "0008_login_admission"
down_revision = "0007_configuration_tenants"
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind(), checkfirst=False)
    op.bulk_insert(slots, [{"slot": 1}, {"slot": 2}])


def downgrade():
    raise RuntimeError("Login admission downgrade is disabled; it would discard shared limits")
