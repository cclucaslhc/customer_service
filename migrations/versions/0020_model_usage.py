"""Durable pre-call usage attempts and immutable final SDK observations."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.model_usage_v1 import add_model_usage, IMMUTABLE_SQL

revision = '0020_model_usage'
down_revision = '0019_trace_context'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    # Immutable FK target stubs permit offline DDL without live reflection.
    sa.Table('agent_runs',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('id',sa.Uuid()))
    sa.Table('identity_memberships',meta,sa.Column('tenant_id',sa.Uuid()),sa.Column('user_id',sa.Uuid()))
    add_model_usage(meta)
    meta.tables['model_usage_attempts'].create(op.get_bind(), checkfirst=False)
    op.execute(IMMUTABLE_SQL)
    op.execute('CREATE TRIGGER cs_model_usage_immutable BEFORE UPDATE OR DELETE ON public.model_usage_attempts FOR EACH ROW EXECUTE FUNCTION public.cs_model_usage_immutable()')
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON public.model_usage_attempts FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Model usage downgrade is disabled: preserve billing evidence')
