"""Real isolated PG scheduler execution, leases, drain and restore disarming."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timezone
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
import sqlalchemy as sa

from app.backup_scheduler import BackupCatalog,BackupPlan,BackupRunner,BackupLeaseLost,BackupAttempt
from app.maintenance import MaintenanceHold
from app.persistence.schema import metadata
from app.persistence.engine import build_engine
from app.recovery import check_operational,RecoveryHold
from app.backup import restore_backup
from app.backup_verifier import LocalRestoreVerifier
from test_logical_backup import backup_setup,empty_database,postgres_database


def plan_for(common,tmp_path,**options):
    packages = tmp_path/'automatic-packages'; packages.mkdir(exist_ok=True)
    return BackupPlan(schedule_id=uuid.uuid4(),interval_seconds=60,binary_dir=common['binary_dir'],
        knowledge_root=common['knowledge_root'],asset_root=common['asset_root'],package_root=packages,
        key_path=common['key_path'],scratch_root=tmp_path,**options)


def test_real_default_empty_and_concurrent_due_slot_has_single_claim(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine)
    assert catalog.claim(plan) is None
    catalog.configure(plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _:catalog.claim(plan),range(2)))
    assert sum(row is not None for row in claims) == 1
    attempt = next(row for row in claims if row)
    wrong = BackupAttempt(attempt.id,uuid.uuid4(),attempt.fence)
    with pytest.raises(BackupLeaseLost):
        catalog.renew(wrong,60)
    catalog.finish(attempt)  # Explicit synthetic unresolved execution.
    assert catalog.claim(plan) is None
    with pytest.raises(BackupLeaseLost,match='review'):
        catalog.configure(plan)


def test_real_automatic_backup_complete_verified_package_and_recovery_disarms_schedule(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    runner = BackupRunner(bundle.engine,common['url'],plan)
    report = runner.run_once()
    assert report['local_verified'] and report['files'] == 2
    check_operational(bundle.engine)
    assert runner.run_once() is None
    with bundle.engine.connect() as db:
        job = db.execute(sa.select(metadata.tables['backup_jobs'])).mappings().one()
        assert job['state'] == 'complete' and job['lease_until'] is None
        assert job['report']['backup_id'] == report['backup_id']
    captured = plan.package_root/report['attempt_id']
    with empty_database() as url:
        result = restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
            'asset_root':tmp_path/'restored-assets'},source=captured,isolated_confirmed=True)
        assert result['backup_schedules_disabled'] == 1 and result['backup_jobs_review_required'] == 1
        engine = build_engine(url,pool_size=1)
        try:
            with engine.connect() as db:
                assert not db.execute(sa.select(metadata.tables['backup_schedules'].c.enabled)).scalar_one()
                assert db.execute(sa.select(metadata.tables['backup_jobs'].c.state)).scalar_one() == 'needs_review'
            with pytest.raises(RecoveryHold):
                BackupCatalog(engine).claim(plan)
        finally:
            engine.dispose()


def test_real_heartbeat_renews_short_job_and_maintenance_leases_during_snapshot(backup_setup,tmp_path,monkeypatch):
    import app.backup_scheduler as scheduler
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path,lease_seconds=10)
    BackupCatalog(bundle.engine).configure(plan)
    original = scheduler.create_backup
    def slow(**kwargs):
        with bundle.engine.connect() as db:
            first = db.execute(sa.select(metadata.tables['backup_jobs'].c.lease_until)).scalar_one()
            maintenance_first = db.execute(sa.select(metadata.tables['maintenance_state'].c.lease_until)).scalar_one()
        deadline = time.monotonic()+5
        renewed = False
        while time.monotonic() < deadline:
            kwargs['maintenance_guard']()
            with bundle.engine.connect() as db:
                next_job = db.execute(sa.select(metadata.tables['backup_jobs'].c.lease_until)).scalar_one()
                next_maintenance = db.execute(sa.select(metadata.tables['maintenance_state'].c.lease_until)).scalar_one()
            if next_job > first and next_maintenance > maintenance_first:
                renewed = True; break
            time.sleep(.05)
        assert renewed
        return original(**kwargs)
    monkeypatch.setattr(scheduler,'create_backup',slow)
    assert BackupRunner(bundle.engine,common['url'],plan).run_once()['local_verified']


def test_real_drain_timeout_preserves_hold_and_catalog_needs_review(backup_setup,tmp_path,monkeypatch):
    import app.backup_scheduler as scheduler
    from app.maintenance import MaintenanceBusy
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path,drain_timeout=1)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    runner = BackupRunner(bundle.engine,common['url'],plan)
    original = runner.maintenance.sealed
    def busy(permit):
        # PG/leases and hold are real. Inject only a permanently busy sealer.
        raise MaintenanceBusy('Synthetic busy executor')
    monkeypatch.setattr(runner.maintenance,'sealed',busy)
    monkeypatch.setattr(scheduler,'create_backup',lambda **kwargs:pytest.fail('Busy source reached snapshot'))
    with pytest.raises(MaintenanceBusy):
        runner.run_once()
    with pytest.raises(MaintenanceHold):
        check_operational(bundle.engine)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(metadata.tables['backup_jobs'].c.state)).scalar_one() == 'needs_review'
    assert not list(plan.package_root.iterdir())


def test_real_expired_job_reaping_is_bounded_and_never_replays(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    attempt = catalog.claim(plan)
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['backup_jobs'].update().values(lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    with pytest.raises(BackupLeaseLost):
        catalog.finish(attempt)
    assert catalog.reap_expired(limit=1) == 1
    assert catalog.reap_expired(limit=1) == 0
    assert catalog.claim(plan) is None
    assert not list(plan.package_root.iterdir())


def test_real_executor_policy_mismatch_rejected_before_claim(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    with pytest.raises(ValueError,match='differs'):
        catalog.claim(replace(plan,drain_timeout=30))
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['backup_jobs'])).scalar_one() == 0


def test_real_independent_worker_consumes_approved_plan_without_implicit_configuration(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    plan = plan_for(common,tmp_path)
    BackupCatalog(bundle.engine).configure(plan)
    path = tmp_path/'plan.json'
    path.write_text(json.dumps({name:str(value) if name in ('schedule_id','binary_dir','knowledge_root','asset_root','package_root','key_path','scratch_root') else value
        for name,value in plan.__dict__.items()}))
    env = dict(os.environ); env['BACKUP_DATABASE_URL'] = common['url']
    result = subprocess.run([sys.executable,'scripts/backup_worker.py','--plan',str(path),'--once','--confirm-global-backup'],
        env=env,capture_output=True,text=True,timeout=40)
    assert result.returncode == 0,result.stderr
    assert json.loads(result.stdout)['local_verified']
    check_operational(bundle.engine)


def test_real_automatic_local_restore_verification_and_exact_owned_database_cleanup(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    root = tmp_path/'verification'; root.mkdir()
    verifier = LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=root,
        isolated_environment_confirmed=True)
    plan = plan_for(common,tmp_path,restore_policy=verifier.policy_fingerprint)
    BackupCatalog(bundle.engine).configure(plan)
    report = BackupRunner(bundle.engine,common['url'],plan,verifier=verifier).run_once()
    assert report['restore_verified'] and report['local_verified']
    admin = build_engine(os.environ['TEST_POSTGRES_URL'],pool_size=1)
    try:
        with admin.connect() as db:
            name = 'cs_backup_verify_'+uuid.UUID(report['attempt_id']).hex
            assert db.execute(sa.text('SELECT count(*) FROM pg_database WHERE datname=:name'),{'name':name}).scalar_one() == 0
    finally:
        admin.dispose()
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(metadata.tables['backup_jobs'].c.report)).scalar_one()['restore_verified']


def test_real_restore_policy_cannot_be_omitted_or_retargeted(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    root = tmp_path/'verification'; root.mkdir()
    verifier = LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=root,
        isolated_environment_confirmed=True)
    plan = plan_for(common,tmp_path,restore_policy=verifier.policy_fingerprint)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    with pytest.raises(ValueError,match='verifier differs'):
        BackupRunner(bundle.engine,common['url'],plan)
    other_root = tmp_path/'other-verification'; other_root.mkdir()
    other = LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=other_root,
        isolated_environment_confirmed=True)
    with pytest.raises(ValueError,match='verifier differs'):
        BackupRunner(bundle.engine,common['url'],plan,verifier=other)
    with pytest.raises(ValueError,match='differs'):
        catalog.claim(replace(plan,restore_policy=None))
    with pytest.raises(ValueError,match='verifier differs'):
        BackupRunner(bundle.engine,common['url'],replace(plan,restore_policy=None),verifier=verifier)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['backup_jobs'])).scalar_one() == 0


def test_real_recurring_execution_has_distinct_packages_and_slots(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    BackupCatalog(bundle.engine).configure(plan)
    runner = BackupRunner(bundle.engine,common['url'],plan)
    first = runner.run_once()
    assert runner.run_once() is None
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['backup_schedules'].update().values(next_due=sa.func.clock_timestamp()))
    second = runner.run_once()
    assert first['attempt_id'] != second['attempt_id'] and first['backup_id'] != second['backup_id']
    assert (plan.package_root/first['attempt_id']/'READY').exists()
    assert (plan.package_root/second['attempt_id']/'READY').exists()
    with bundle.engine.connect() as db:
        jobs = db.execute(sa.select(metadata.tables['backup_jobs'])).mappings().all()
        assert len(jobs) == 2 and all(job['state'] == 'complete' for job in jobs)
        assert len({job['scheduled_for'] for job in jobs}) == 2


def test_real_catalog_rejects_incomplete_success_report(backup_setup,tmp_path):
    bundle,common,*_ = backup_setup
    plan = plan_for(common,tmp_path)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    attempt = catalog.claim(plan)
    with pytest.raises(ValueError,match='report'):
        catalog.finish(attempt,report={'local_verified':True})
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(metadata.tables['backup_jobs'].c.state)).scalar_one() == 'running'
    catalog.finish(attempt)


def test_real_failed_restore_validation_retains_owned_database_and_blocks_replay(backup_setup,tmp_path,monkeypatch):
    import app.backup_verifier as verification
    bundle,common,*_ = backup_setup
    root = tmp_path/'verification'; root.mkdir()
    verifier = LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=root,
        isolated_environment_confirmed=True)
    plan = plan_for(common,tmp_path,restore_policy=verifier.policy_fingerprint)
    catalog = BackupCatalog(bundle.engine); catalog.configure(plan)
    original = verification.restore_backup
    def rejected(**kwargs):
        result = original(**kwargs)  # Actual PG restoration before fault injection.
        return {**result,'verified':False}
    monkeypatch.setattr(verification,'restore_backup',rejected)
    runner = BackupRunner(bundle.engine,common['url'],plan,verifier=verifier)
    with pytest.raises(ValueError,match='execution hold'):
        runner.run_once()
    with bundle.engine.connect() as db:
        job = db.execute(sa.select(metadata.tables['backup_jobs'])).mappings().one()
        assert job['state'] == 'needs_review' and job['report'] is None
    assert runner.run_once() is None
    check_operational(bundle.engine)  # Source maintenance was released before isolated restore.
    name = 'cs_backup_verify_'+job['id'].hex
    assert (root/job['id'].hex/'knowledge').is_dir()
    admin = build_engine(os.environ['TEST_POSTGRES_URL'],pool_size=1)
    try:
        with admin.connect() as db:
            assert db.execute(sa.text('SELECT count(*) FROM pg_database WHERE datname=:name'),{'name':name}).scalar_one() == 1
        target = build_engine(admin.url.set(database=name),pool_size=1)
        try:
            with target.connect() as db:
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one()
                assert not db.execute(sa.select(metadata.tables['backup_schedules'].c.enabled)).scalar_one()
        finally:
            target.dispose()
    finally:
        # Only this synthetic fixture's exact UUID database is eligible for cleanup.
        assert name == 'cs_backup_verify_'+uuid.UUID(str(job['id'])).hex
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
            db.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()
