"""PostgreSQL-backed shared model circuit with fenced half-open probes."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import uuid

import sqlalchemy as sa

from app.persistence.schema import metadata
from app.settings import MODEL_RUN_TIMEOUT_SECONDS


FAILURE_WINDOW = timedelta(seconds=60)
OPEN_TIME = timedelta(seconds=30)
# A probe must outlive the whole bounded model Run. Cross-node deployments
# must use the same timeout configuration; stale completion is still fenced.
PROBE_LEASE = timedelta(seconds=max(120, MODEL_RUN_TIMEOUT_SECONDS) + 30)
FAILURE_THRESHOLD = 3


class ModelCircuitOpen(RuntimeError):
    """A shared circuit denied this outbound model attempt."""


@dataclass(frozen=True)
class CircuitPermit:
    generation: int
    owner: uuid.UUID | None = None


class ModelCircuit:
    def __init__(self, meter):
        self.meter = meter
        self.table = metadata.tables['model_circuit_state']

    def _locked(self, db, tenant):
        identity = f'{tenant}:{self.meter.provider}:{self.meter.model}'
        key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'big') & ((1 << 63)-1)
        db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key':key})
        return db.execute(sa.select(self.table).where(
            self.table.c.tenant_id == tenant,
            self.table.c.provider == self.meter.provider,
            self.table.c.model == self.meter.model).with_for_update()).mappings().first()

    def acquire(self):
        meter = self.meter
        with meter.chat.engine.begin() as db:
            run, _ = meter._run(db)
            tenant = run['tenant_id']
            row = self._locked(db, tenant)
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            if row is None:
                db.execute(self.table.insert().values(tenant_id=tenant,provider=meter.provider,
                    model=meter.model,state='closed',failure_count=0,open_count=0,window_start=None,
                    open_until=None,probe_owner=None,probe_until=None,generation=0,updated_at=now))
                return CircuitPermit(0)
            if row['state'] == 'closed':
                return CircuitPermit(row['generation'])
            if row['state'] == 'open' and now < row['open_until']:
                raise ModelCircuitOpen('Shared model circuit is open')
            if row['state'] == 'half_open' and now < row['probe_until']:
                raise ModelCircuitOpen('Shared model probe is in progress')
            owner = uuid.uuid4()
            generation = row['generation'] + 1
            db.execute(self.table.update().where(self.table.c.tenant_id == tenant,
                self.table.c.provider == meter.provider,self.table.c.model == meter.model).values(
                    state='half_open',failure_count=0,window_start=None,open_until=None,
                    probe_owner=owner,probe_until=now+PROBE_LEASE,
                    generation=generation,updated_at=now))
            return CircuitPermit(generation,owner)

    def report(self, permit, *, healthy, failure=False):
        """Only health failures count; stale reports cannot close a newer open circuit."""
        meter = self.meter
        with meter.chat.engine.begin() as db:
            run, _ = meter._run(db,finishing=True)
            tenant = run['tenant_id']
            row = self._locked(db,tenant)
            if row is None or row['generation'] != permit.generation:
                return
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            where = (self.table.c.tenant_id == tenant,
                     self.table.c.provider == meter.provider,self.table.c.model == meter.model)
            if row['state'] == 'half_open':
                if row['probe_owner'] != permit.owner:
                    return
                values = (dict(state='closed',failure_count=0,window_start=None,
                               open_until=None,probe_owner=None,probe_until=None)
                          if healthy else dict(state='open',failure_count=FAILURE_THRESHOLD,
                               open_count=row['open_count']+1,window_start=now,open_until=now+OPEN_TIME,
                               probe_owner=None,probe_until=None))
                db.execute(self.table.update().where(*where).values(
                    **values,generation=row['generation']+1,updated_at=now))
                return
            if row['state'] != 'closed':
                return
            if healthy:
                db.execute(self.table.update().where(*where).values(
                    failure_count=0,window_start=None,updated_at=now))
            elif failure:
                fresh = row['window_start'] is not None and now-row['window_start'] <= FAILURE_WINDOW
                count = min(FAILURE_THRESHOLD,(row['failure_count'] if fresh else 0)+1)
                values = dict(failure_count=count,window_start=row['window_start'] if fresh else now,
                              updated_at=now)
                if count == FAILURE_THRESHOLD:
                    values.update(state='open',open_until=now+OPEN_TIME,
                                  open_count=row['open_count']+1,
                                  generation=row['generation']+1)
                db.execute(self.table.update().where(*where).values(**values))

    def abort(self, permit):
        # An unused half-open probe does not prove recovery. Cool down again.
        if permit.owner is not None:
            self.report(permit,healthy=False)
