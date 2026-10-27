"""Immutable 0018 schedules and fenced backup execution catalog."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_backup_schedule(meta):
    sa.Table('backup_schedules',meta,
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('interval_seconds',sa.Integer(),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('next_due',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('interval_seconds BETWEEN 60 AND 604800',name='backup_interval'),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'",name='backup_policy_hash'))
    jobs = sa.Table('backup_jobs',meta,
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('schedule_id',sa.Uuid(),sa.ForeignKey('backup_schedules.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('scheduled_for',sa.DateTime(timezone=True),nullable=False),
        sa.Column('owner',sa.Uuid(),nullable=False),
        sa.Column('fence',sa.BigInteger(),nullable=False),
        sa.Column('lease_until',sa.DateTime(timezone=True),nullable=True),
        sa.Column('state',sa.Text(),nullable=False),
        sa.Column('report',JSONB(none_as_null=True),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True),nullable=True),
        sa.UniqueConstraint('schedule_id','scheduled_for',name='uq_backup_schedule_slot'),
        sa.CheckConstraint("state IN ('running','complete','needs_review') AND fence > 0",name='backup_job_state'),
        sa.CheckConstraint("(state='running' AND lease_until IS NOT NULL AND finished_at IS NULL) OR (state<>'running' AND lease_until IS NULL AND finished_at IS NOT NULL)",name='backup_job_lease'),
        sa.CheckConstraint("report IS NULL OR (jsonb_typeof(report)='object' AND octet_length(report::text)<=16384)",name='backup_report_budget'))
    sa.Index('uq_backup_global_running',jobs.c.state,unique=True,postgresql_where=sa.text("state='running'"))
