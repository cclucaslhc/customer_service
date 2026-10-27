"""Protected rollout state; routing and controller are separate implementation gates."""
from alembic import op
import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.rag_rollout_v1 import add_rag_rollouts

revision = '0032_rag_rollouts'
down_revision = '0031_rag_release_intents'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('rag_release_intents', meta, sa.Column('tenant_id', sa.Uuid(), primary_key=True),
        sa.Column('release_id', sa.Uuid(), primary_key=True))
    add_rag_rollouts(meta).create(op.get_bind(), checkfirst=False)
    op.execute('REVOKE ALL ON public.rag_rollouts FROM PUBLIC')
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
        'ON public.rag_rollouts FOR EACH STATEMENT EXECUTE FUNCTION public.cs_maintenance_write_guard()')
    op.execute("""CREATE FUNCTION public.cs_rag_rollout_definition_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF (NEW.tenant_id,NEW.rollout_id,NEW.release_id,NEW.plan,NEW.plan_sha256,NEW.created_at,NEW.expires_at)
             IS DISTINCT FROM
             (OLD.tenant_id,OLD.rollout_id,OLD.release_id,OLD.plan,OLD.plan_sha256,OLD.created_at,OLD.expires_at)
          THEN RAISE EXCEPTION 'RAG rollout definition is immutable'; END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'RAG rollout revision must advance exactly once';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER cs_rag_rollout_definition BEFORE UPDATE ON public.rag_rollouts '
        'FOR EACH ROW EXECUTE FUNCTION public.cs_rag_rollout_definition_guard()')


def downgrade():
    raise RuntimeError('RAG rollout downgrade is disabled: preserve rollout evidence')
