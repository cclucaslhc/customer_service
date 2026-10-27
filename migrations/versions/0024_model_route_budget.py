"""Versioned per-model fallback token contracts under the existing global budget."""

from alembic import op
import sqlalchemy as sa

from app.persistence.schema_v1 import metadata as baseline
from app.persistence.model_route_budget_v1 import add_model_route_budget

revision = '0024_model_route_budget'
down_revision = '0023_knowledge_trace'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_memberships', meta, sa.Column('tenant_id', sa.Uuid()),
             sa.Column('user_id', sa.Uuid()))
    sa.Table('model_budget_reservations', meta, sa.Column('tenant_id', sa.Uuid()))
    add_model_route_budget(meta)
    meta.tables['model_route_budget_policies'].create(op.get_bind(), checkfirst=False)
    op.add_column('model_budget_reservations', sa.Column('route_policy_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_budget_reservation_route_policy', 'model_budget_reservations',
                          'model_route_budget_policies', ['tenant_id', 'route_policy_id'],
                          ['tenant_id', 'route_policy_id'], ondelete='RESTRICT')
    name = 'model_route_budget_policies'
    op.execute(f'CREATE TRIGGER cs_route_budget_immutable BEFORE UPDATE OR DELETE ON public.{name} '
               'FOR EACH ROW EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
    op.execute(f'CREATE TRIGGER cs_route_budget_no_truncate BEFORE TRUNCATE ON public.{name} '
               'FOR EACH STATEMENT EXECUTE FUNCTION public.cs_cost_evidence_no_change()')
    op.execute(f'CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.{name} '
               'FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Route budget downgrade is disabled: preserve billing evidence')
