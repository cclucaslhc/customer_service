"""Versioned private object inputs for cross-host knowledge parsing."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = '0015_knowledge_inputs'
down_revision = '0014_recovery_hold'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_job_items', sa.Column('input_object', JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint('knowledge_input_object_shape', 'knowledge_job_items',
        "input_object IS NULL OR (jsonb_typeof(input_object) = 'object' AND sha256 IS NOT NULL)")


def downgrade():
    raise RuntimeError('Knowledge input downgrade is disabled: dropping references loses queued input authority')
