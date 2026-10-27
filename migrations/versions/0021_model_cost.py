"""Explicit tenant prices, pinned quotes, estimates and operator reconciliations."""
from alembic import op
import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.model_cost_v1 import add_model_cost, NO_CHANGE_SQL

revision='0021_model_cost'
down_revision='0020_model_usage'
branch_labels=None
depends_on=None


def upgrade():
    meta=sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_memberships',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('user_id',sa.Uuid()))
    sa.Table('model_usage_attempts',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('attempt_id',sa.Uuid()))
    add_model_cost(meta)
    op.create_unique_constraint('uq_model_usage_tenant_attempt','model_usage_attempts',['tenant_id','attempt_id'])
    for name in ('model_price_versions','model_price_quotes','model_cost_assessments'):
        meta.tables[name].create(op.get_bind(),checkfirst=False)
    op.execute(NO_CHANGE_SQL)
    for name in ('model_price_versions','model_price_quotes','model_cost_assessments'):
        op.execute(f'CREATE TRIGGER cs_cost_evidence_immutable BEFORE UPDATE OR DELETE ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
        op.execute(f'CREATE TRIGGER cs_cost_evidence_no_truncate BEFORE TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
        op.execute(f'CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')
    op.execute('CREATE TRIGGER cs_cost_evidence_no_truncate BEFORE TRUNCATE ON public.model_usage_attempts FOR EACH STATEMENT EXECUTE FUNCTION public.cs_cost_evidence_no_change()')


def downgrade():
    raise RuntimeError('Model cost downgrade is disabled: preserve price and reconciliation evidence')
