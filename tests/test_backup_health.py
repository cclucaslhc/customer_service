"""Real PG read-only freshness + actual backup, with synthetic corruption faults."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import sys
import uuid

import pytest
import sqlalchemy as sa

from app.backup_health import BackupHealth,prometheus,publish_metrics
from app.backup_scheduler import BackupCatalog,BackupRunner
from app.backup_verifier import LocalRestoreVerifier
from app.persistence.schema import metadata
from test_backup_scheduler import plan_for
from test_logical_backup import backup_setup,postgres_database


def inspect(bundle,plan,**options):
    return BackupHealth(bundle.engine).inspect(schedule_id=plan.schedule_id,policy_fingerprint=plan.fingerprint,
        max_age_seconds=3600,**options)


def test_real_absent_empty_or_wrong_policy_never_healthy(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    result = inspect(bundle,plan)
    assert not result['healthy'] and not result['policy_matches']
    BackupCatalog(bundle.engine).configure(plan)
    result = inspect(bundle,plan)
    assert result['policy_matches'] and result['schedule_enabled'] and not result['healthy']
    assert all(item['snapshot_timestamp'] is None for item in result['evidence'].values())
    wrong = inspect(bundle,replace(plan,drain_timeout=30))
    assert not wrong['policy_matches'] and not wrong['healthy']


def test_real_local_backup_does_not_prove_remote_or_restore(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    BackupCatalog(bundle.engine).configure(plan)
    run = BackupRunner(bundle.engine,common['url'],plan).run_once()
    result = inspect(bundle,plan)
    assert result['evidence']['local']['fresh'] and not result['healthy']
    assert result['evidence']['restore']['snapshot_timestamp'] is None
    assert result['evidence']['remote']['snapshot_timestamp'] is None
    assert inspect(bundle,plan,require_restore=False)['healthy']
    assert not inspect(bundle,plan,require_restore=False,require_remote=True)['healthy']
    wrong = inspect(bundle,replace(plan,drain_timeout=30),require_restore=False)
    assert not wrong['healthy'] and not wrong['evidence']['local']['fresh']
    assert result['evidence']['local']['snapshot_timestamp'] == datetime.fromisoformat(run['snapshot_at']).timestamp()
    metrics = prometheus(result)
    assert 'cs_backup_verification_fresh{kind="local"} 1' in metrics
    assert 'cs_backup_verification_required{kind="restore"} 1' in metrics
    assert str(plan.schedule_id) not in metrics and str(run['attempt_id']) not in metrics


def test_real_actual_restore_verification_and_disabled_recovery_hold(backup_setup,tmp_path):
    import os
    bundle,common,*_ = backup_setup
    root = tmp_path/'verification'; root.mkdir()
    verifier = LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=root,
        isolated_environment_confirmed=True)
    plan = plan_for(common,tmp_path,restore_policy=verifier.policy_fingerprint)
    BackupCatalog(bundle.engine).configure(plan)
    BackupRunner(bundle.engine,common['url'],plan,verifier=verifier).run_once()
    assert inspect(bundle,plan)['healthy']
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['backup_schedules'].update().values(enabled=False))
    result = inspect(bundle,plan)
    assert not result['healthy'] and not result['evidence']['restore']['fresh']
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['backup_schedules'].update().values(enabled=True))
        db.execute(metadata.tables['recovery_state'].insert().values(id=1,blocked=True,
            backup_id=uuid.uuid4(),restored_at=sa.func.clock_timestamp()))
    result = inspect(bundle,plan)
    assert result['recovery_hold'] and not result['healthy']


def test_real_snapshot_not_completion_controls_freshness_and_bad_records_fail_closed(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    BackupCatalog(bundle.engine).configure(plan)
    BackupRunner(bundle.engine,common['url'],plan).run_once()
    jobs = metadata.tables['backup_jobs']
    with bundle.engine.begin() as db:
        original = db.execute(sa.select(jobs.c.report)).scalar_one()
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        db.execute(jobs.update().values(report={**original,'snapshot_at':(now-timedelta(seconds=7200)).isoformat()},
            finished_at=now))
    result = inspect(bundle,plan,require_restore=False)
    assert not result['healthy'] and result['evidence']['local']['age_seconds'] >= 7200
    for bad in ('not-a-date',datetime.now().replace(tzinfo=None).isoformat(),
                datetime(2099,1,1,tzinfo=timezone.utc).isoformat()):
        with bundle.engine.begin() as db:
            db.execute(jobs.update().values(report={**original,'snapshot_at':bad}))
        result = inspect(bundle,plan,require_restore=False)
        assert result['invalid_reports'] == 1 and not result['healthy']


def test_real_expired_and_review_job_does_not_get_repaired_by_monitor(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    attempt = catalog.claim(plan)
    jobs = metadata.tables['backup_jobs']
    with bundle.engine.begin() as db:
        db.execute(jobs.update().values(lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    result = inspect(bundle,plan)
    assert result['expired_running'] == 1 and not result['healthy']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(jobs.c.state)).scalar_one() == 'running'
    assert catalog.reap_expired() == 1
    assert inspect(bundle,plan)['needs_review'] == 1


def test_monitor_rejects_implicit_or_unbounded_policies():
    engine = sa.create_engine('postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic')
    try:
        for bound in (0,True,2592001):
            with pytest.raises(ValueError,match='policy'):
                BackupHealth(engine).inspect(schedule_id=uuid.uuid4(),policy_fingerprint='a'*64,max_age_seconds=bound)
    finally:
        engine.dispose()


def test_failed_collection_metrics_never_publish_stale_success(monkeypatch,capsys):
    import scripts.backup_health as cli
    monkeypatch.setattr(sys,'argv',['backup_health.py','--schedule-id',str(uuid.uuid4()),
        '--policy-fingerprint','a'*64,'--max-age-seconds','3600','--format','prometheus'])
    monkeypatch.setattr(cli,'build_engine',lambda *a,**k:pytest.fail('Unauthorized command connected to database'))
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert 'cs_backup_catalog_collection_success 0' in captured.out
    assert 'cs_backup_verified_snapshot_timestamp_seconds' not in captured.out


def test_real_independent_cli_unhealthy_exit_and_no_sensitive_output(backup_setup,tmp_path,monkeypatch,capsys):
    import scripts.backup_health as cli
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    BackupCatalog(bundle.engine).configure(plan)
    monkeypatch.setenv('BACKUP_DATABASE_URL',common['url'])
    monkeypatch.setattr(sys,'argv',['backup_health.py','--schedule-id',str(plan.schedule_id),
        '--policy-fingerprint',plan.fingerprint,'--max-age-seconds','3600','--confirm-global-catalog-read'])
    assert cli.main() == 2
    captured = capsys.readouterr()
    assert not json.loads(captured.out)['healthy'] and not captured.err
    assert 'local-integration-only' not in captured.out


def test_atomic_failed_collection_replaces_previous_health_and_uses_lf(tmp_path):
    destination = tmp_path/'customer-service-backup.prom'
    destination.write_bytes(b'Synthetic previous healthy sample\n')
    publish_metrics(destination)
    assert destination.read_bytes() == prometheus().encode('utf-8')
    assert b'\r' not in destination.read_bytes()
    assert not list(tmp_path.glob('.cs-backup-metrics-*'))


def test_atomic_publisher_failure_retains_previous_file_and_cleans_own_temp(tmp_path,monkeypatch):
    import app.backup_health as health
    destination = tmp_path/'customer-service-backup.prom'
    destination.write_bytes(b'Synthetic preserved sample\n')
    def failed(*args):
        raise OSError('Synthetic rename failure')
    monkeypatch.setattr(health.os,'replace',failed)
    with pytest.raises(OSError):
        publish_metrics(destination)
    assert destination.read_bytes() == b'Synthetic preserved sample\n'
    assert not list(tmp_path.glob('.cs-backup-metrics-*'))
    with pytest.raises(ValueError,match='dedicated'):
        publish_metrics(tmp_path/'unrelated.prom')
