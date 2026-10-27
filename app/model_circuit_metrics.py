"""Private low-cardinality aggregates for durable shared model circuits."""

from time import perf_counter

import sqlalchemy as sa

from app.metrics_export import MetricsRenderLimit
from app.persistence.schema import metadata


STATES = ('closed','open','half_open')


class ModelCircuitMetrics:
    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Model circuit metrics require PostgreSQL')
        self.engine = engine

    @staticmethod
    def _check(deadline, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise MetricsRenderLimit('cancelled')
        if deadline is not None and perf_counter() >= deadline:
            raise MetricsRenderLimit('deadline')

    def snapshot(self, *, deadline=None, cancel_event=None):
        self._check(deadline,cancel_event)
        table = metadata.tables['model_circuit_state']
        try:
            with self.engine.begin() as db:
                db.execute(sa.text('SET TRANSACTION READ ONLY'))
                db.execute(sa.text("SET LOCAL statement_timeout='750ms'"))
                db.execute(sa.text("SET LOCAL lock_timeout='100ms'"))
                collected = db.execute(sa.select(sa.func.extract('epoch',sa.func.clock_timestamp()))).scalar_one()
                rows = db.execute(sa.select(table.c.state,sa.func.count(),sa.func.sum(table.c.open_count))
                    .group_by(table.c.state)).all()
            self._check(deadline,cancel_event)
            counts = dict.fromkeys(STATES,0)
            openings = 0
            for state, count, opened in rows:
                if state not in counts:
                    raise ValueError('Unknown model circuit state')
                counts[state] = count
                openings += opened or 0
            return {'success':1,'collected_at':float(collected),'states':counts,'openings':openings}
        except MetricsRenderLimit:
            raise
        except Exception:
            return {'success':0,'collected_at':0.,'states':dict.fromkeys(STATES,0),'openings':0}

    def prometheus(self, *, deadline=None, cancel_event=None):
        value = self.snapshot(deadline=deadline,cancel_event=cancel_event)
        lines = [
            '# HELP customer_service_model_circuit_collection_success Whether durable circuit aggregation succeeded.',
            '# TYPE customer_service_model_circuit_collection_success gauge',
            f'customer_service_model_circuit_collection_success {value["success"]}',
            '# HELP customer_service_model_circuit_collection_timestamp_seconds Database time of successful aggregation.',
            '# TYPE customer_service_model_circuit_collection_timestamp_seconds gauge',
            f'customer_service_model_circuit_collection_timestamp_seconds {value["collected_at"]}',
            '# HELP customer_service_model_circuit_states Number of tenant-model circuits in each state.',
            '# TYPE customer_service_model_circuit_states gauge',
        ]
        lines.extend(f'customer_service_model_circuit_states{{state="{state}"}} {value["states"][state]}'
                     for state in STATES)
        lines.extend([
            '# HELP customer_service_model_circuit_openings_total Durable aggregate openings and reopenings.',
            '# TYPE customer_service_model_circuit_openings_total counter',
            f'customer_service_model_circuit_openings_total {value["openings"]}',
        ])
        return lines
