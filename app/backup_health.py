"""Read-only backup catalog freshness. No artifact checks, repair or secret I/O."""
from datetime import datetime,timezone
import os
from pathlib import Path
import re
import tempfile
import uuid

import sqlalchemy as sa
from app.persistence.schema import metadata
from app.backup import checked_directory


KINDS = ('local','remote','restore')


def _timestamp(report,finished,now):
    if not isinstance(report,dict) or report.get('local_verified') is not True:
        raise ValueError('Invalid backup verification record')
    uuid.UUID(report['backup_id'])
    value = datetime.fromisoformat(report['snapshot_at'])
    if value.utcoffset() is None or finished.utcoffset() is None:
        raise ValueError('Backup times require timezone')
    value = value.astimezone(timezone.utc)
    if value > finished or finished > now:
        raise ValueError('Future backup record cannot prove freshness')
    return value


class BackupHealth:
    def __init__(self,engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Backup monitoring requires PostgreSQL')
        self.engine = engine

    def inspect(self,*,schedule_id,policy_fingerprint,max_age_seconds,
                require_remote=False,require_restore=True):
        schedule_id = uuid.UUID(str(schedule_id))
        if (not isinstance(policy_fingerprint,str)
                or re.fullmatch('[0-9a-f]{64}',policy_fingerprint) is None
                or type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 2592000
                or type(require_remote) is not bool or type(require_restore) is not bool):
            raise ValueError('Explicit bounded backup monitoring policy required')
        schedules,jobs = (metadata.tables[name] for name in ('backup_schedules','backup_jobs'))
        # One consistent read-only snapshot: hold must remain observable, not repaired.
        with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
            with db.begin():
                db.exec_driver_sql('SET TRANSACTION READ ONLY')
                db.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
                now = db.execute(sa.select(sa.func.transaction_timestamp())).scalar_one()
                schedule = db.execute(sa.select(schedules).where(schedules.c.id == schedule_id)).mappings().first()
                review = db.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.state == 'needs_review')).scalar_one()
                expired = db.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.state == 'running',jobs.c.lease_until <= now)).scalar_one()
                running = db.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.state == 'running')).scalar_one()
                holds = {}
                for name in ('maintenance_state','recovery_state'):
                    holds[name] = bool(db.execute(sa.select(metadata.tables[name].c.blocked)).scalar_one_or_none())
                # Bounded history; never infer success from records omitted by a limit.
                rows = db.execute(sa.select(jobs.c.report,jobs.c.finished_at).where(
                    jobs.c.schedule_id == schedule_id,jobs.c.state == 'complete')
                    .order_by(jobs.c.finished_at.desc()).limit(101)).mappings().all()
        matches = bool(schedule and schedule['fingerprint'] == policy_fingerprint)
        enabled = bool(schedule and schedule['enabled'])
        truncated = len(rows) > 100
        latest = {kind:None for kind in KINDS}
        invalid = 0
        for row in rows[:100]:
            try:
                report = row['report']
                snapshot = _timestamp(report,row['finished_at'],now)
                for flag in ('remote_verified','restore_verified'):
                    if flag in report and type(report[flag]) is not bool:
                        raise ValueError('Invalid verification flag')
                if report.get('remote_verified') is True:
                    uuid.UUID(report['archive_id'])
                    if type(report.get('chunks')) is not int or report['chunks'] < 1:
                        raise ValueError('Invalid archive proof')
                kinds = ['local']
                if report.get('remote_verified') is True:
                    kinds.append('remote')
                if report.get('restore_verified') is True:
                    elapsed = report.get('restore_elapsed_seconds')
                    if type(elapsed) not in (int,float) or not 0 <= elapsed < float('inf'):
                        raise ValueError('Invalid restore proof')
                    kinds.append('restore')
                for kind in kinds:
                    if latest[kind] is None or snapshot > latest[kind]:
                        latest[kind] = snapshot
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
                invalid += 1
        evidence = {}
        for kind,snapshot in latest.items():
            age = (now-snapshot).total_seconds() if snapshot else None
            evidence[kind] = {'snapshot_timestamp':snapshot.timestamp() if snapshot else None,
                'age_seconds':age,'fresh':bool(matches and enabled and age is not None and age <= max_age_seconds)}
        required = ['local'] + (['remote'] if require_remote else []) + (['restore'] if require_restore else [])
        healthy = (matches and enabled and not review and not expired and not invalid
            and not holds['recovery_state'] and all(evidence[kind]['fresh'] for kind in required))
        return {'collection_success':True,'collected_at':now.timestamp(),'policy_matches':matches,'schedule_enabled':enabled,
            'max_age_seconds':max_age_seconds,'require_remote':require_remote,'require_restore':require_restore,
            'needs_review':review,'expired_running':expired,'running':running,'invalid_reports':invalid,
            'history_truncated':truncated,'maintenance_hold':holds['maintenance_state'],
            'recovery_hold':holds['recovery_state'],'evidence':evidence,'healthy':bool(healthy)}


def prometheus(report=None):
    """Fixed low-cardinality labels; failure output never republishes stale success."""
    values = [('cs_backup_catalog_collection_success',int(report is not None),{})]
    if report is not None:
        for metric,key in (('cs_backup_policy_matches','policy_matches'),('cs_backup_schedule_enabled','schedule_enabled'),
            ('cs_backup_health','healthy'),('cs_backup_jobs_needs_review','needs_review'),
            ('cs_backup_jobs_expired','expired_running'),('cs_backup_jobs_running','running'),
            ('cs_backup_invalid_reports','invalid_reports'),('cs_backup_history_truncated','history_truncated'),
            ('cs_backup_maintenance_hold','maintenance_hold'),('cs_backup_recovery_hold','recovery_hold'),
            ('cs_backup_collection_timestamp_seconds','collected_at'),('cs_backup_max_age_seconds','max_age_seconds')):
            values.append((metric,int(report[key]) if isinstance(report[key],bool) else report[key],{}))
        for kind in KINDS:
            evidence = report['evidence'][kind]
            required = kind == 'local' or report['require_'+kind]
            values.extend((('cs_backup_verification_required',int(required),{'kind':kind}),
                ('cs_backup_verification_fresh',int(evidence['fresh']),{'kind':kind}),
                ('cs_backup_verified_snapshot_timestamp_seconds',evidence['snapshot_timestamp'] or 0,{'kind':kind})))
    output = []
    seen = set()
    for metric,value,labels in values:
        if metric not in seen:
            output.extend((f'# HELP {metric} Read-only catalog evidence; not a live artifact availability check.',f'# TYPE {metric} gauge'))
            seen.add(metric)
        suffix = '{'+','.join(f'{key}="{value}"' for key,value in labels.items())+'}' if labels else ''
        output.append(f'{metric}{suffix} {value}')
    return '\n'.join(output)+'\n'


def publish_metrics(path:Path,report=None):
    """Atomic dedicated textfile publication, including unhealthy/failure samples."""
    if not path.is_absolute() or path.name != 'customer-service-backup.prom' or path.is_symlink():
        raise ValueError('Explicit dedicated backup metrics path required')
    parent = checked_directory(path.parent,existing=True)
    if path.exists() and not path.is_file():
        raise ValueError('Backup metrics destination must be a regular file')
    descriptor,name = tempfile.mkstemp(prefix='.cs-backup-metrics-',dir=parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor,'wb') as target:
            target.write(prometheus(report).encode('utf-8'))
            target.flush(); os.fsync(target.fileno())
        if path.is_symlink():
            raise ValueError('Backup metrics destination changed')
        os.replace(temporary,parent/path.name)
    finally:
        if temporary.exists():
            temporary.unlink()
