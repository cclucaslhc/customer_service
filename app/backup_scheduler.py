"""Explicit PostgreSQL backup scheduling, lease heartbeats and maintenance drain.

No default schedules, credentials, production bootstrap or automatic replay of
unknown attempts. Source files are copied only under the real maintenance seal.
"""
from dataclasses import dataclass
from datetime import datetime,timedelta
import hashlib
import json
import math
import re
from pathlib import Path
import threading
import time
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from app.persistence.schema import metadata
from app.persistence.maintenance_v1 import WRITE_GATE_KEY
from app.recovery import require_operational,RecoveryHold
from app.maintenance import Maintenance,MaintenanceBusy
from app.backup import create_backup,checked_directory,_validated_url
from app.backup_archive import verify_package,archive_package
from app.backup import _key

CATALOG_KEY = 1129533507


class BackupLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupAttempt:
    id: uuid.UUID
    owner: uuid.UUID
    fence: int


@dataclass(frozen=True)
class BackupPlan:
    schedule_id: uuid.UUID
    interval_seconds: int
    binary_dir: Path
    knowledge_root: Path
    asset_root: Path
    package_root: Path
    key_path: Path
    scratch_root: Path
    drain_timeout: int = 120
    lease_seconds: int = 60
    restore_policy: str | None = None
    object_policy: str | None = None
    archive_policy: str | None = None
    archive_namespace: str | None = None
    catalog_policy: str | None = None

    def validate(self):
        if type(self.interval_seconds) is not int or not 60 <= self.interval_seconds <= 604800:
            raise ValueError('Invalid backup schedule interval')
        if type(self.drain_timeout) is not int or not 1 <= self.drain_timeout <= 600:
            raise ValueError('Invalid drain timeout')
        Maintenance._seconds(self.lease_seconds)
        for fingerprint in (self.restore_policy,self.object_policy,self.archive_policy,self.catalog_policy):
            if fingerprint is not None and (not isinstance(fingerprint,str)
                    or re.fullmatch(r'[0-9a-f]{64}',fingerprint) is None):
                raise ValueError('Invalid backup policy fingerprint')
        if (self.archive_policy is None) != (self.archive_namespace is None):
            raise ValueError('Archive policy and namespace required together')
        if self.archive_namespace is not None and str(uuid.UUID(self.archive_namespace)) != self.archive_namespace:
            raise ValueError('Canonical archive namespace required')
        if self.catalog_policy is not None and (self.archive_policy is None or self.catalog_policy == self.archive_policy):
            raise ValueError('Catalog checkpoint requires a distinct approved store and remote archive')
        for name in ('binary_dir','knowledge_root','asset_root','package_root','scratch_root'):
            checked_directory(getattr(self,name),existing=True)
        if not self.key_path.is_absolute():
            raise ValueError('Explicit backup key path required')
        # Construction/fingerprinting never reads the key; encryption verifies it.
        uuid.UUID(str(self.schedule_id))

    @property
    def fingerprint(self):
        value = {name:str(getattr(self,name)) for name in self.__dataclass_fields__}
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


class BackupCatalog:
    def __init__(self,engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Backup catalog requires explicit PostgreSQL')
        self.engine = engine
        self.schedules,self.jobs = (metadata.tables[name] for name in ('backup_schedules','backup_jobs'))

    def _lock(self,db):
        db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':CATALOG_KEY})

    def configure(self,plan):
        plan.validate()
        with self.engine.begin() as db:
            db.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':WRITE_GATE_KEY})
            require_operational(db)
            self._lock(db)
            if db.execute(sa.select(self.jobs.c.id).where(self.jobs.c.state.in_(['running','needs_review'])).limit(1)).first():
                raise BackupLeaseLost('Unresolved backup execution requires review')
            row = db.execute(sa.select(self.schedules).where(self.schedules.c.id == plan.schedule_id)).mappings().first()
            if row and (row['fingerprint'] != plan.fingerprint or row['interval_seconds'] != plan.interval_seconds):
                raise ValueError('Backup schedule policy is immutable; review migration before changing it')
            db.execute(insert(self.schedules).values(id=plan.schedule_id,fingerprint=plan.fingerprint,
                interval_seconds=plan.interval_seconds,enabled=True,next_due=sa.func.clock_timestamp())
                .on_conflict_do_update(index_elements=[self.schedules.c.id],set_={'enabled':True}))

    def claim(self,plan):
        plan.validate()
        with self.engine.begin() as db:
            db.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':WRITE_GATE_KEY})
            require_operational(db)
            self._lock(db)
            if db.execute(sa.select(self.jobs.c.id).where(self.jobs.c.state.in_(['running','needs_review'])).limit(1)).first():
                return None
            row = db.execute(sa.select(self.schedules).where(self.schedules.c.id == plan.schedule_id).with_for_update()).mappings().first()
            if not row or not row['enabled']:
                return None
            if row['fingerprint'] != plan.fingerprint:
                raise ValueError('Executor policy differs from approved schedule')
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if row['next_due'] > now:
                return None
            attempt = BackupAttempt(uuid.uuid4(),uuid.uuid4(),1)
            db.execute(self.jobs.insert().values(id=attempt.id,schedule_id=plan.schedule_id,scheduled_for=row['next_due'],
                owner=attempt.owner,fence=attempt.fence,state='running',created_at=now,
                lease_until=now+timedelta(seconds=plan.lease_seconds)))
            # Collapse missed intervals rather than enqueue an unbounded backlog.
            db.execute(self.schedules.update().where(self.schedules.c.id == plan.schedule_id).values(
                next_due=now+timedelta(seconds=plan.interval_seconds)))
        return attempt

    def _current(self,db,attempt):
        if db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one_or_none():
            raise RecoveryHold()
        row = db.execute(sa.select(self.jobs).where(self.jobs.c.id == attempt.id)).mappings().first()
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if (not row or row['state'] != 'running' or row['owner'] != attempt.owner or row['fence'] != attempt.fence
                or row['lease_until'] <= now):
            raise BackupLeaseLost('Backup attempt no longer owns execution')
        return row

    def check(self,attempt):
        with self.engine.connect() as db:
            self._current(db,attempt)

    def renew(self,attempt,seconds):
        Maintenance._seconds(seconds)
        with self.engine.begin() as db:
            db.execute(sa.select(self.jobs.c.id).where(self.jobs.c.id == attempt.id).with_for_update())
            self._current(db,attempt)
            db.execute(self.jobs.update().where(self.jobs.c.id == attempt.id).values(
                lease_until=sa.func.clock_timestamp()+sa.text(f"interval '{seconds} seconds'")))

    def finish(self,attempt,*,report=None):
        allowed = {'backup_id','schema','files','objects','snapshot_at','elapsed_seconds','local_verified',
                   'archive_id','remote_verified','chunks','restore_verified','restore_elapsed_seconds','restore_bucket',
                   'catalog_verified','catalog_record_id','catalog_reference'}
        if report is not None:
            required = {'backup_id','schema','files','objects','snapshot_at','elapsed_seconds','local_verified'}
            if (not isinstance(report,dict) or set(report)-allowed or required-set(report)
                    or len(json.dumps(report,allow_nan=False).encode()) > 8192):
                raise ValueError('Invalid bounded backup report')
            uuid.UUID(report['backup_id'])
            if datetime.fromisoformat(report['snapshot_at']).utcoffset() is None:
                raise ValueError('Backup snapshot must include timezone')
            if not isinstance(report['schema'],str) or not report['schema'] or report['local_verified'] is not True:
                raise ValueError('Backup completion requires local verification')
            for name in ('files','objects','chunks'):
                if name in report and (type(report[name]) is not int or report[name] < 0):
                    raise ValueError('Invalid backup count')
            for name in ('elapsed_seconds','restore_elapsed_seconds'):
                if name in report and (type(report[name]) not in (int,float)
                        or not math.isfinite(report[name]) or report[name] < 0):
                    raise ValueError('Invalid backup duration')
            for flag,fields in (('remote_verified',{'archive_id','chunks'}),
                                ('restore_verified',{'restore_elapsed_seconds'})):
                if flag in report or fields.intersection(report):
                    if report.get(flag) is not True or fields-set(report):
                        raise ValueError('Incomplete backup verification report')
            if 'archive_id' in report:
                uuid.UUID(report['archive_id'])
            if 'restore_bucket' in report and (report.get('restore_verified') is not True
                    or not isinstance(report['restore_bucket'],str)
                    or re.fullmatch('[a-z0-9][a-z0-9-]{2,28}[a-z0-9]-[0-9a-f]{32}',report['restore_bucket']) is None):
                raise ValueError('Invalid isolated restore bucket report')
            if {'catalog_verified','catalog_record_id','catalog_reference'}.intersection(report):
                if report.get('catalog_verified') is not True or report.get('remote_verified') is not True:
                    raise ValueError('Catalog checkpoint completion required')
                uuid.UUID(report['catalog_record_id'])
                from app.object_store import ObjectReference
                ObjectReference.from_dict(report['catalog_reference'])
        with self.engine.begin() as db:
            db.execute(sa.select(self.jobs.c.id).where(self.jobs.c.id == attempt.id).with_for_update())
            self._current(db,attempt)
            db.execute(self.jobs.update().where(self.jobs.c.id == attempt.id).values(
                state='complete' if report is not None else 'needs_review',report=report,
                lease_until=None,finished_at=sa.func.clock_timestamp()))

    def reap_expired(self,limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid backup reaping bound')
        with self.engine.begin() as db:
            if db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one_or_none():
                raise RecoveryHold()
            self._lock(db)
            rows = list(db.execute(sa.select(self.jobs.c.id).where(self.jobs.c.state == 'running',
                self.jobs.c.lease_until <= sa.func.clock_timestamp()).with_for_update(skip_locked=True).limit(limit)).scalars())
            if rows:
                db.execute(self.jobs.update().where(self.jobs.c.id.in_(rows)).values(
                    state='needs_review',lease_until=None,finished_at=sa.func.clock_timestamp()))
            return len(rows)


class _Heartbeat:
    def __init__(self,catalog,attempt,maintenance,seconds):
        self.catalog,self.attempt,self.maintenance,self.seconds = catalog,attempt,maintenance,seconds
        self.permit = None
        self.lock,self.stop,self.failed = threading.Lock(),threading.Event(),threading.Event()
        self.thread = threading.Thread(target=self._run,daemon=True,name='backup-lease-heartbeat')

    def _run(self):
        while not self.stop.wait(1):
            try:
                self.catalog.renew(self.attempt,self.seconds)
                with self.lock:
                    if self.permit is not None:
                        self.maintenance.renew(self.permit,seconds=self.seconds)
            except Exception:
                self.failed.set(); return

    def check(self):
        if self.failed.is_set():
            raise BackupLeaseLost('Backup heartbeat failed')
        self.catalog.check(self.attempt)

    def close(self):
        self.stop.set(); self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise BackupLeaseLost('Backup heartbeat teardown unconfirmed')


class BackupRunner:
    def __init__(self,engine,url,plan,*,object_store=None,archive_store=None,archive_namespace=None,verifier=None,catalog_store=None):
        _validated_url(url)
        if engine.url.set(password=None) != sa.engine.make_url(url).set(password=None):
            raise ValueError('Backup database must match the catalog connection')
        plan.validate()
        if (archive_store is None) != (archive_namespace is None):
            raise ValueError('Explicit archive store and namespace required together')
        for store,expected in ((object_store,plan.object_policy),(archive_store,plan.archive_policy),(catalog_store,plan.catalog_policy)):
            actual = None if store is None else store.policy_fingerprint
            if actual != expected:
                raise ValueError('Storage destination differs from approved backup policy')
        if archive_namespace != plan.archive_namespace:
            raise ValueError('Archive namespace differs from approved backup policy')
        actual_policy = None if verifier is None else verifier.policy_fingerprint
        if actual_policy != plan.restore_policy:
            raise ValueError('Restore verifier differs from approved backup policy')
        self.catalog,self.maintenance = BackupCatalog(engine),Maintenance(engine)
        self.url,self.plan,self.object_store = url,plan,object_store
        self.archive_store,self.archive_namespace = archive_store,archive_namespace
        self.verifier = verifier
        self.catalog_store = catalog_store

    def run_once(self):
        attempt = self.catalog.claim(self.plan)
        if attempt is None:
            return None
        heartbeat = _Heartbeat(self.catalog,attempt,self.maintenance,self.plan.lease_seconds)
        heartbeat.thread.start()
        package = self.plan.package_root/str(attempt.id)
        try:
            with heartbeat.lock:
                heartbeat.permit = self.maintenance.request(seconds=self.plan.lease_seconds)
            deadline = time.monotonic()+self.plan.drain_timeout
            while True:
                heartbeat.check()
                started = False
                try:
                    with self.maintenance.sealed(heartbeat.permit) as seal_guard:
                        started = True
                        def guard():
                            heartbeat.check(); seal_guard()
                        report = create_backup(url=self.url,binary_dir=self.plan.binary_dir,
                            knowledge_root=self.plan.knowledge_root,asset_root=self.plan.asset_root,destination=package,
                            key_path=self.plan.key_path,scratch_root=self.plan.scratch_root,maintenance_confirmed=True,
                            object_store=self.object_store,maintenance_guard=guard)
                        guard()
                        verify_package(package,_key(self.plan.key_path),self.plan.scratch_root)
                        guard()
                    break
                except MaintenanceBusy:
                    if started or time.monotonic() >= deadline:
                        raise
                    heartbeat.stop.wait(.25)
            heartbeat.check()
            with heartbeat.lock:
                self.maintenance.release(heartbeat.permit)
                heartbeat.permit = None
            report['local_verified'] = True
            if self.archive_store is not None:
                archived = archive_package(package=package,receipt=self.plan.package_root/(str(attempt.id)+'.receipt.json'),
                    store=self.archive_store,namespace=self.archive_namespace,key_path=self.plan.key_path,
                    scratch_root=self.plan.scratch_root,guard=heartbeat.check)
                report.update({name:archived[name] for name in ('archive_id','remote_verified','chunks')})
            if self.verifier is not None:
                report.update(self.verifier.verify(attempt_id=attempt.id,package=package,plan=self.plan,guard=heartbeat.check))
                if report.get('restore_verified') is not True:
                    raise ValueError('Approved restore verification did not complete')
            if self.catalog_store is not None:
                from app.backup_catalog_archive import publish_checkpoint
                report.update(publish_checkpoint(store=self.catalog_store,archive_store=self.archive_store,
                    namespace=self.archive_namespace,plan=self.plan,attempt=attempt,report=report,
                    receipt_path=self.plan.package_root/(str(attempt.id)+'.receipt.json'),
                    anchor_path=self.plan.package_root/(str(attempt.id)+'.catalog.json'),
                    key_path=self.plan.key_path,scratch_root=self.plan.scratch_root,guard=heartbeat.check))
            heartbeat.close()
            heartbeat.check()
            self.catalog.finish(attempt,report=report)
            return {'attempt_id':str(attempt.id),**report}
        except Exception:
            # Do not auto-release maintenance, retry the command, or delete a
            # possibly complete package/remote versions after an unknown result.
            try:
                heartbeat.close()
                self.catalog.finish(attempt)
            except Exception:
                pass  # Expired/unknown catalog result stays for bounded reaping.
            raise
