"""Typed immutable upload/reparse manifest fields; old path imports unchanged."""
from alembic import op
import sqlalchemy as sa

revision = '0030_knowledge_actions'
down_revision = '0029_knowledge_admission'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('LOCK TABLE public.knowledge_jobs IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.knowledge_jobs WHERE execution_mode='queued' "
               "AND status='running') THEN RAISE EXCEPTION 'Drain knowledge Workers before 0030'; "
               "END IF; END $$")
    op.add_column('knowledge_job_items',sa.Column('display_name',sa.Text()))
    op.add_column('knowledge_job_items',sa.Column('replace_document_id',sa.Uuid()))
    op.add_column('knowledge_job_items',sa.Column('replace_document_version',sa.Integer()))
    op.create_check_constraint('knowledge_action_manifest','knowledge_job_items',
        '(display_name IS NULL OR length(display_name) BETWEEN 1 AND 255) AND '
        '((replace_document_id IS NULL AND replace_document_version IS NULL) OR '
        '(replace_document_id IS NOT NULL AND replace_document_version IS NOT NULL AND replace_document_version > 0))')
    op.execute("""CREATE FUNCTION public.cs_knowledge_action_claim_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.status='pending' AND NEW.status='running' AND NEW.execution_mode='queued'
             AND EXISTS(SELECT 1 FROM public.knowledge_job_items i WHERE i.job_id=NEW.id
                        AND (i.display_name IS NOT NULL OR i.replace_document_id IS NOT NULL))
             AND coalesce(current_setting('cs.knowledge_action_version',true),'') <> '1' THEN
            RAISE EXCEPTION 'Knowledge action requires current Worker';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER cs_knowledge_action_claim BEFORE UPDATE OF status ON public.knowledge_jobs '
               'FOR EACH ROW EXECUTE FUNCTION public.cs_knowledge_action_claim_guard()')


def downgrade():
    raise RuntimeError('Knowledge action downgrade is disabled: preserve pinned reparse targets')
