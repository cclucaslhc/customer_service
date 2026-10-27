"""Atomic application budget policies/reservations; no supplier bill guarantee."""
from alembic import op
import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.model_budget_v1 import add_model_budget

revision='0022_model_budget'
down_revision='0021_model_cost'
branch_labels=None
depends_on=None


def upgrade():
    meta=sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_memberships',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('user_id',sa.Uuid()))
    sa.Table('model_price_quotes',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('attempt_id',sa.Uuid()))
    add_model_budget(meta)
    for name in ('model_budget_policies','model_budget_reservations'):
        meta.tables[name].create(op.get_bind(),checkfirst=False)
        op.execute(f'CREATE TRIGGER cs_budget_evidence_immutable BEFORE UPDATE OR DELETE ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
        op.execute(f'CREATE TRIGGER cs_budget_evidence_no_truncate BEFORE TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
        op.execute(f'CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.{name} FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Budget downgrade is disabled: preserve reserved cost evidence')
