"""Persistent global maintenance and transaction-scoped write fencing."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.maintenance_v1 import add_maintenance, PROTECTED_TABLES, WRITE_FUNCTION_SQL

revision = '0017_maintenance_gate'
down_revision = '0016_storage_configs'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    add_maintenance(meta)
    meta.tables['maintenance_state'].create(op.get_bind(),checkfirst=False)
    op.execute(WRITE_FUNCTION_SQL)
    for name in PROTECTED_TABLES:
        op.execute(f'CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public."{name}" FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Maintenance downgrade is disabled: write fencing cannot be removed')
