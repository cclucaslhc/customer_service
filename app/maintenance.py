"""Trusted global maintenance, expiring owner permits and sealed database writes.

Expiry NEVER reopens service. Recovery hold cannot be released here. No tenant
HTTP control or credentials discovery; future scheduler must own the full permit.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import uuid
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from app.persistence.schema import metadata
from app.persistence.maintenance_current import WRITE_GATE_KEY,PROTECTED_TABLES
from app.recovery import RecoveryHold


class MaintenanceHold(RuntimeError):
    def __init__(self):
        super().__init__('Database maintenance pending authorized completion')


class MaintenanceConflict(RuntimeError):
    pass


class MaintenanceBusy(MaintenanceConflict):
    pass


@dataclass(frozen=True)
class MaintenancePermit:
    owner: uuid.UUID
    fence: int


class Maintenance:
    def __init__(self,engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Maintenance requires explicit PostgreSQL')
        self.engine,self.table = engine,metadata.tables['maintenance_state']

    @staticmethod
    def _seconds(seconds):
        if type(seconds) is not int or not 10 <= seconds <= 3600:
            raise ValueError('Maintenance lease must be 10–3600 seconds')
        return seconds

    def request(self,*,seconds=60,takeover_expired=False):
        seconds = self._seconds(seconds)
        owner = uuid.uuid4()
        with self.engine.begin() as db:
            # Existing writers hold the shared lock until commit/rollback. The
            # exclusive request cannot publish a hold ahead of their commit.
            db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':WRITE_GATE_KEY})
            if db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one_or_none():
                raise RecoveryHold()
            row = db.execute(sa.select(self.table).with_for_update()).mappings().first()
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if row and row['blocked'] and (row['lease_until'] > now or not takeover_expired):
                raise MaintenanceConflict('Existing maintenance needs its owner or explicit expired takeover')
            fence = (row['fence'] if row else 0)+1
            values = dict(id=1,blocked=True,owner=owner,fence=fence,
                lease_until=sa.func.clock_timestamp()+sa.text(f"interval '{seconds} seconds'"))
            db.execute(insert(self.table).values(**values).on_conflict_do_update(index_elements=[self.table.c.id],set_=values))
        return MaintenancePermit(owner,fence)

    def _current(self,db,permit):
        row = db.execute(sa.select(self.table).where(self.table.c.id == 1)).mappings().first()
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if (not row or not row['blocked'] or row['owner'] != permit.owner or row['fence'] != permit.fence
                or row['lease_until'] <= now):
            raise MaintenanceConflict('Maintenance owner expired or was replaced')
        if db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one_or_none():
            raise RecoveryHold()
        return row

    def check(self,permit):
        with self.engine.connect() as db:
            self._current(db,permit)

    def renew(self,permit,*,seconds=60):
        seconds = self._seconds(seconds)
        with self.engine.begin() as db:
            db.execute(sa.select(self.table.c.id).where(self.table.c.id == 1).with_for_update())
            self._current(db,permit)
            db.execute(self.table.update().where(self.table.c.id == 1).values(
                lease_until=sa.func.clock_timestamp()+sa.text(f"interval '{seconds} seconds'")))

    def release(self,permit):
        with self.engine.begin() as db:
            db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':WRITE_GATE_KEY})
            db.execute(sa.select(self.table.c.id).where(self.table.c.id == 1).with_for_update())
            self._current(db,permit)
            db.execute(self.table.update().where(self.table.c.id == 1).values(blocked=False,owner=None,lease_until=None))

    @contextmanager
    def sealed(self,permit):
        """Only one live sealing transaction; no SQL/model retries or implicit release.

        Tables are SHARE-locked even for SQL clients that bypass triggers. Raw
        nextval/DDL/superuser actions are outside this application's role boundary.
        Active authorized Worker leases must have drained before taking a snapshot.
        """
        with self.engine.begin() as db:
            if not db.execute(sa.text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':WRITE_GATE_KEY}).scalar_one():
                raise MaintenanceBusy('Another writer or sealing transaction is still active')
            self._current(db,permit)
            names = ','.join('public."'+name+'"' for name in PROTECTED_TABLES)
            db.exec_driver_sql('LOCK TABLE '+names+' IN SHARE MODE')
            for name,lease in (('agent_runs','lease_until'),('knowledge_jobs','lease_until')):
                table = metadata.tables[name]
                if db.execute(sa.select(sa.func.count()).select_from(table).where(table.c.status == 'running',
                    sa.or_(table.c[lease].is_(None),table.c[lease] > sa.func.clock_timestamp()))).scalar_one():
                    raise MaintenanceBusy('Active or unleased execution has not drained')
            def guard():
                # This connection still owns the sealing transaction/locks.
                self._current(db,permit)
            guard()
            yield guard
            guard()
