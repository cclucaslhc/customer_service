"""Write-once per-run route snapshot; commit fencing consumes this durable value."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0034_run_rag_route'
down_revision='0033_rag_route_projection'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('agent_runs',sa.Column('rag_route',JSONB(),nullable=True))
    op.create_check_constraint(op.f('ck_agent_runs_rag_route_shape'),'agent_runs',
        "rag_route IS NULL OR (jsonb_typeof(rag_route)='object' AND octet_length(rag_route::text)<=4096)")
    op.execute("""CREATE FUNCTION public.cs_run_rag_route_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.rag_route IS NOT NULL AND NEW.rag_route IS DISTINCT FROM OLD.rag_route THEN
            RAISE EXCEPTION 'Run RAG route is immutable';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER cs_run_rag_route BEFORE UPDATE ON public.agent_runs '
        'FOR EACH ROW EXECUTE FUNCTION public.cs_run_rag_route_guard()')


def downgrade():
    raise RuntimeError('Run route downgrade is disabled: preserve routing evidence')
