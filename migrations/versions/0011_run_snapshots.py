"""Atomic terminal snapshots and event retention watermark; no implicit purge."""
import sqlalchemy as sa
from alembic import op
from app.persistence.schema_v1 import metadata as baseline
from app.persistence.run_snapshot_v1 import add_run_snapshots

revision = '0011_run_snapshots'
down_revision = '0010_durable_chat'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    for table in baseline.tables.values():
        table.to_metadata(meta)
    runs = meta.tables['agent_runs']
    runs.append_column(sa.Column('tenant_id',sa.Uuid()))
    runs.append_constraint(sa.UniqueConstraint('tenant_id','id'))
    runs.append_column(sa.Column('execution_mode',sa.Text()))
    runs.append_column(sa.Column('event_sequence',sa.BigInteger()))
    add_run_snapshots(meta)
    op.add_column('agent_runs',sa.Column('event_data_bytes',sa.BigInteger(),nullable=False,server_default='0'))
    op.add_column('agent_runs',sa.Column('event_floor',sa.BigInteger(),nullable=False,server_default='0'))
    op.create_check_constraint('run_event_accounting','agent_runs','event_data_bytes >= 0 AND event_floor >= 0 AND event_floor <= event_sequence')
    op.create_index('ix_agent_runs_event_retention','agent_runs',['finished_at','id'],
        postgresql_where=sa.text("execution_mode = 'queued' AND status IN ('complete','error','interrupted') AND event_floor < event_sequence"))
    meta.tables['chat_run_snapshots'].create(op.get_bind(),checkfirst=False)
    # Migration runs under the migration lock with old writers stopped. Preserve
    # exact JSONB data accounting and materialize existing 0010 final results.
    op.execute("""UPDATE agent_runs r SET event_data_bytes = (
        SELECT coalesce(sum(octet_length(e.data::text)),0) FROM chat_run_events e
        WHERE e.run_id=r.id AND e.tenant_id=r.tenant_id)""")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_runs WHERE execution_mode='queued' AND status IN ('queued','running')
          AND (event_data_bytes > 3145728 OR event_sequence > 9000)) THEN
          RAISE EXCEPTION 'Active Run event budget requires reconciliation before upgrade';
        END IF;
        END $$""")
    op.execute("""INSERT INTO chat_run_snapshots(tenant_id,run_id,sequence,state,created_at)
        SELECT r.tenant_id,r.id,r.event_sequence,
        jsonb_build_object('draft',CASE WHEN r.status='complete' THEN coalesce(m.content,'') ELSE '' END,
          'citations',CASE WHEN r.status='complete' THEN coalesce(m.citations_json,'[]'::jsonb) ELSE '[]'::jsonb END,
          'message_id',CASE WHEN r.status='complete' THEN m.id::text ELSE NULL END,
          'contact_summary',CASE WHEN r.status='complete' THEN (
            SELECT e.data->>'summary' FROM chat_run_events e WHERE e.run_id=r.id AND e.tenant_id=r.tenant_id
              AND e.event='contact_form' ORDER BY e.sequence DESC LIMIT 1) ELSE NULL END),
        coalesce(r.finished_at,clock_timestamp())
        FROM agent_runs r LEFT JOIN messages m ON m.id=(SELECT (e.data->>'message_id')::uuid
          FROM chat_run_events e WHERE e.run_id=r.id AND e.tenant_id=r.tenant_id AND e.event='done'
          ORDER BY e.sequence DESC LIMIT 1) AND m.tenant_id=r.tenant_id AND m.session_id=r.session_id AND m.role='assistant'
        WHERE r.execution_mode='queued' AND r.status IN ('complete','error','interrupted')""")
    # Emit the validation in offline SQL too; never skip integrity gates just
    # because Alembic is generating a reviewable deployment script.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM chat_run_snapshots s JOIN agent_runs r ON r.id=s.run_id
            WHERE r.status='complete' AND s.state->>'message_id' IS NULL) THEN
            RAISE EXCEPTION 'Cannot snapshot a completed Run without its authoritative assistant message';
        END IF;
        END $$""")


def downgrade():
    raise RuntimeError('Run snapshot downgrade is disabled: retained results and event watermarks must be preserved')
