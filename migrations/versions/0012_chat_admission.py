"""Immutable shared admission policy and durable rate watermark; opt-in only."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.chat_admission_v1 import add_chat_admission

revision = '0012_chat_admission'
down_revision = '0011_run_snapshots'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    for table in baseline.tables.values():
        table.to_metadata(meta)
    add_chat_admission(meta)
    op.add_column('agent_runs',sa.Column('admission_started_at',sa.DateTime(timezone=True)))
    op.add_column('agent_runs',sa.Column('admission_until',sa.DateTime(timezone=True)))
    op.create_index('ix_agent_runs_admission_until','agent_runs',['admission_until'],
        postgresql_where=sa.text('admission_until IS NOT NULL'))
    op.create_index('ix_agent_runs_admission_started','agent_runs',['admission_started_at'],
        postgresql_where=sa.text('admission_started_at IS NOT NULL'))
    meta.tables['chat_admission_policy'].create(op.get_bind(),checkfirst=False)


def downgrade():
    raise RuntimeError('Admission downgrade is disabled: shared policy and rate history must be preserved')
