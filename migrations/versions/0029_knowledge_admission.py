"""Durable global/tenant parser reservations; upgrade requires drained Workers."""
from alembic import op
import sqlalchemy as sa
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.knowledge_admission_v1 import add_knowledge_admission

revision = '0029_knowledge_admission'
down_revision = '0028_rag_vector'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('LOCK TABLE public.knowledge_jobs IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.knowledge_jobs WHERE execution_mode='queued' "
               "AND status='running') THEN RAISE EXCEPTION 'Drain knowledge Workers before 0029'; "
               "END IF; END $$")
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    jobs = sa.Table('knowledge_jobs', meta, sa.Column('tenant_id', sa.Uuid()))
    table = add_knowledge_admission(meta)
    op.add_column('knowledge_jobs', sa.Column('execution_slot_held',sa.Boolean(),
                                             nullable=False,server_default=sa.false()))
    op.create_check_constraint('knowledge_execution_slot_owner', 'knowledge_jobs',
        "NOT execution_slot_held OR (execution_mode='queued' AND lease_owner IS NOT NULL AND lease_fence>0)")
    op.create_index('ix_knowledge_jobs_held_slots', 'knowledge_jobs', ['tenant_id'],
                    postgresql_where=sa.text('execution_slot_held'))
    table.create(op.get_bind(), checkfirst=False)
    op.bulk_insert(table, [dict(id=1, capacity=2, tenant_capacity=1)])
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
               'ON public.knowledge_admission_policy FOR EACH STATEMENT '
               'EXECUTE FUNCTION public.cs_maintenance_write_guard()')
    op.execute("""CREATE FUNCTION public.cs_knowledge_claim_slot_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.status='pending' AND NEW.status='running' AND NEW.execution_mode='queued'
             AND NOT NEW.execution_slot_held THEN
            RAISE EXCEPTION 'Knowledge claim requires a capacity reservation';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER cs_knowledge_claim_slot BEFORE UPDATE OF status ON public.knowledge_jobs '
               'FOR EACH ROW EXECUTE FUNCTION public.cs_knowledge_claim_slot_guard()')


def downgrade():
    raise RuntimeError('Knowledge admission downgrade is disabled: preserve abandoned reservations')
