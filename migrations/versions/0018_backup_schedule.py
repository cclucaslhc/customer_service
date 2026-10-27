"""Default-empty backup schedule and execution catalog; no auto maintenance."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.backup_schedule_v1 import add_backup_schedule

revision = '0018_backup_schedule'
down_revision = '0017_maintenance_gate'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    add_backup_schedule(meta)
    meta.create_all(op.get_bind(),checkfirst=False)


def downgrade():
    raise RuntimeError('Backup schedule downgrade is disabled: execution and recovery evidence must be retained')
