"""Owner-controlled phase clock; pause existing running rollouts before upgrade."""
from alembic import op
import sqlalchemy as sa

revision='0037_rag_phase_clock'
down_revision='0036_rag_runtime_binding'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.rag_rollouts WHERE state='running') THEN
      RAISE EXCEPTION 'Pause all running RAG rollouts before phase-clock migration'; END IF; END $$""")
    op.add_column('rag_rollouts',sa.Column('step_started_at',sa.DateTime(timezone=True),nullable=True))
    op.execute("CREATE INDEX ix_agent_runs_rag_epoch ON public.agent_runs "
        "(tenant_id,(rag_route->>'rollout_id'),(rag_route->>'route_version'),(rag_route->>'index_id')) "
        "WHERE rag_route IS NOT NULL")
    op.create_check_constraint('rag_rollout_phase_clock','rag_rollouts',
        "(state<>'running' OR step_started_at IS NOT NULL) AND "
        "(step_started_at IS NULL OR (step_started_at>=created_at AND step_started_at<expires_at))")
    op.execute("""CREATE FUNCTION public.cs_rag_phase_clock_guard() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.step_started_at IS DISTINCT FROM OLD.step_started_at THEN
        IF NEW.state<>'running' OR NEW.step_started_at IS NULL
          OR NEW.step_started_at>clock_timestamp()
          OR NEW.step_started_at<statement_timestamp()
          OR (OLD.step_started_at IS NOT NULL AND NEW.step_started_at<OLD.step_started_at)
          OR NOT (OLD.state='staged' OR (OLD.state='running' AND NEW.step_index=OLD.step_index+1))
        THEN RAISE EXCEPTION 'Invalid RAG phase clock change'; END IF;
      ELSIF NEW.state='running' AND (OLD.state<>'running' OR NEW.step_index<>OLD.step_index) THEN
        RAISE EXCEPTION 'Fresh RAG phase clock required';
      END IF;
      RETURN NEW;
      END $$""")
    op.execute('CREATE TRIGGER cs_rag_phase_clock BEFORE UPDATE ON public.rag_rollouts '
        'FOR EACH ROW EXECUTE FUNCTION public.cs_rag_phase_clock_guard()')


def downgrade():
    raise RuntimeError('Phase-clock downgrade is disabled: preserve observation provenance')
