"""Persist session-local message order independently of clocks and UUIDs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_message_order"
down_revision = "0001_legacy_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("sequence", sa.BigInteger(), nullable=True))
    # Existing PostgreSQL baseline has no insertion ordinal. Preserve its previous
    # timestamp/UUID order, not an invented historical order. Snapshot imports at
    # this revision instead use SQLite's timestamp/rowid order.
    op.execute("""WITH ordered AS (
        SELECT id, row_number() OVER (PARTITION BY session_id ORDER BY created_at, id) AS ordinal
        FROM messages
    ) UPDATE messages SET sequence = ordered.ordinal FROM ordered WHERE messages.id = ordered.id""")
    op.alter_column("messages", "sequence", nullable=False)
    op.create_unique_constraint("uq_messages_session_sequence", "messages", ["session_id", "sequence"])
    op.create_check_constraint("message_sequence_positive", "messages", "sequence > 0")


def downgrade():
    raise RuntimeError("Message ordering downgrade is disabled; use a compatible application rollback")
