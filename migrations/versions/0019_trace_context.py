"""Nullable internal queue trace carrier; no request content or headers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.persistence.trace_context_v1 import CHECK

revision = '0019_trace_context'
down_revision = '0018_backup_schedule'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_run_queue',sa.Column('trace_context',JSONB(none_as_null=True),nullable=True))
    op.create_check_constraint('queue_trace_context','chat_run_queue',CHECK)


def downgrade():
    raise RuntimeError('Trace context downgrade is disabled: retain execution correlation evidence')
