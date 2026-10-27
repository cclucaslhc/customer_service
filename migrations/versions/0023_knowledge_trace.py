"""Nullable internal knowledge queue trace carrier; old jobs stay unlinked."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.persistence.trace_context_v1 import CHECK

revision = '0023_knowledge_trace'
down_revision = '0022_model_budget'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_job_queue',sa.Column('trace_context',JSONB(none_as_null=True),nullable=True))
    op.create_check_constraint('knowledge_queue_trace_context','knowledge_job_queue',CHECK)


def downgrade():
    raise RuntimeError('Knowledge trace context downgrade is disabled: retain execution correlation evidence')
