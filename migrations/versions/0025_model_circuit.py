"""Shared per-tenant model circuit state for multi-instance routing."""

from alembic import op
import sqlalchemy as sa

from app.persistence.schema_v1 import metadata as baseline
from app.persistence.model_circuit_v1 import add_model_circuit

revision = '0025_model_circuit'
down_revision = '0024_model_route_budget'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_tenants', meta, sa.Column('id', sa.Uuid(), primary_key=True))
    add_model_circuit(meta)
    meta.tables['model_circuit_state'].create(op.get_bind(), checkfirst=False)
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
               'ON public.model_circuit_state FOR EACH STATEMENT '
               'EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Model circuit downgrade is disabled: do not silently erase live coordination')
