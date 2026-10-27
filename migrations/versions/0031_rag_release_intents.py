"""Protected durable release-intent storage; no automatic approval or execution."""
from alembic import op
import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.rag_release_v1 import add_rag_release_intents

revision='0031_rag_release_intents'
down_revision='0030_knowledge_actions'
branch_labels=None
depends_on=None


def upgrade():
    meta=sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_memberships',meta,sa.Column('tenant_id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),primary_key=True))
    sa.Table('knowledge_index_versions',meta,sa.Column('tenant_id',sa.Uuid(),primary_key=True),
        sa.Column('index_id',sa.Uuid(),primary_key=True))
    add_rag_release_intents(meta).create(op.get_bind(),checkfirst=False)
    op.execute('REVOKE ALL ON public.rag_release_intents FROM PUBLIC')
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
        'ON public.rag_release_intents FOR EACH STATEMENT '
        'EXECUTE FUNCTION public.cs_maintenance_write_guard()')
    op.execute("""CREATE FUNCTION public.cs_rag_release_definition_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF (NEW.tenant_id,NEW.release_id,NEW.candidate_index_id,NEW.previous_index_id,
              NEW.reviewer_a,NEW.reviewer_b,NEW.body,NEW.body_sha256,NEW.created_at,NEW.expires_at)
             IS DISTINCT FROM
             (OLD.tenant_id,OLD.release_id,OLD.candidate_index_id,OLD.previous_index_id,
              OLD.reviewer_a,OLD.reviewer_b,OLD.body,OLD.body_sha256,OLD.created_at,OLD.expires_at)
          THEN RAISE EXCEPTION 'RAG release definition is immutable'; END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'RAG release revision must advance exactly once';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER cs_rag_release_definition BEFORE UPDATE ON public.rag_release_intents '
        'FOR EACH ROW EXECUTE FUNCTION public.cs_rag_release_definition_guard()')


def downgrade():
    raise RuntimeError('RAG release-intent downgrade is disabled: preserve approval evidence')
