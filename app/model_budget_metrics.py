"""Low-cardinality aggregate budget metrics from durable PostgreSQL evidence."""
from time import perf_counter

import sqlalchemy as sa

from app.metrics_export import MetricsRenderLimit
from app.persistence.schema import metadata


class ModelBudgetMetrics:
    """No tenant/model/currency/amount labels or values leave the private endpoint."""
    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Budget metrics require PostgreSQL')
        self.engine = engine

    @staticmethod
    def _check(deadline, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise MetricsRenderLimit('cancelled')
        if deadline is not None and perf_counter() >= deadline:
            raise MetricsRenderLimit('deadline')

    def snapshot(self, *, deadline=None, cancel_event=None):
        self._check(deadline,cancel_event)
        policies=metadata.tables['model_budget_policies']
        reservations=metadata.tables['model_budget_reservations']
        attempts=metadata.tables['model_usage_attempts']
        costs=metadata.tables['model_cost_assessments']
        runs=metadata.tables['agent_runs']
        try:
            with self.engine.begin() as db:
                db.execute(sa.text('SET TRANSACTION READ ONLY'))
                db.execute(sa.text("SET LOCAL statement_timeout='750ms'"))
                db.execute(sa.text("SET LOCAL lock_timeout='100ms'"))
                collected=db.execute(sa.select(sa.func.extract('epoch',sa.func.clock_timestamp()))).scalar_one()
                policy_count=db.execute(sa.select(sa.func.count(sa.distinct(policies.c.tenant_id)))).scalar_one()
                reservation_count=db.execute(sa.select(sa.func.count()).select_from(reservations)).scalar_one()
                tenant_soft=db.execute(sa.select(sa.func.count(sa.distinct(reservations.c.tenant_id))).where(
                    reservations.c.tenant_soft)).scalar_one()
                day_soft=db.execute(sa.select(sa.func.count()).select_from(sa.select(
                    reservations.c.tenant_id,sa.cast(sa.func.timezone('UTC',attempts.c.started_at),sa.Date)
                ).select_from(reservations.join(attempts,sa.and_(
                    reservations.c.tenant_id==attempts.c.tenant_id,
                    reservations.c.attempt_id==attempts.c.attempt_id))).where(
                    reservations.c.day_soft).distinct().subquery())).scalar_one()
                run_soft=db.execute(sa.select(sa.func.count(sa.distinct(attempts.c.run_id))).select_from(
                    reservations.join(attempts,sa.and_(reservations.c.tenant_id==attempts.c.tenant_id,
                        reservations.c.attempt_id==attempts.c.attempt_id))).where(reservations.c.run_soft)).scalar_one()
                hard=db.execute(sa.select(sa.func.count()).select_from(runs).where(
                    runs.c.error_code=='budget_exceeded')).scalar_one()
                unavailable=db.execute(sa.select(sa.func.count()).select_from(runs).where(
                    runs.c.error_code=='budget_unavailable')).scalar_one()
                known=sa.exists(sa.select(costs.c.assessment_id).where(
                    costs.c.tenant_id==attempts.c.tenant_id,costs.c.attempt_id==attempts.c.attempt_id,
                    costs.c.kind=='estimate',costs.c.status=='estimated'))
                reserved=sa.exists(sa.select(reservations.c.attempt_id).where(
                    reservations.c.tenant_id==attempts.c.tenant_id,
                    reservations.c.attempt_id==attempts.c.attempt_id))
                protected=sa.exists(sa.select(policies.c.policy_id).where(
                    policies.c.tenant_id==attempts.c.tenant_id))
                unresolved=db.execute(sa.select(sa.func.count(sa.distinct(attempts.c.tenant_id))).where(
                    protected,~reserved,~known)).scalar_one()
            self._check(deadline,cancel_event)
            return {'success':1,'collected_at':float(collected),'policies':policy_count,
                'reservations':reservation_count,'soft':{'tenant':tenant_soft,'day':day_soft,'run':run_soft},
                'rejections':{'exceeded':hard,'unavailable':unavailable},'unresolved_tenants':unresolved}
        except MetricsRenderLimit:
            raise
        except Exception:
            # Failure is observable without exporting raw database errors.
            return {'success':0,'collected_at':0.,'policies':0,'reservations':0,
                'soft':{'tenant':0,'day':0,'run':0},'rejections':{'exceeded':0,'unavailable':0},
                'unresolved_tenants':0}

    def prometheus(self, *, deadline=None, cancel_event=None):
        value=self.snapshot(deadline=deadline,cancel_event=cancel_event)
        lines=[
            '# HELP customer_service_model_budget_collection_success Whether the durable budget aggregate query succeeded.',
            '# TYPE customer_service_model_budget_collection_success gauge',
            f'customer_service_model_budget_collection_success {value["success"]}',
            '# HELP customer_service_model_budget_collection_timestamp_seconds Database timestamp of the last successful scrape query.',
            '# TYPE customer_service_model_budget_collection_timestamp_seconds gauge',
            f'customer_service_model_budget_collection_timestamp_seconds {value["collected_at"]}',
            '# HELP customer_service_model_budget_policies Number of tenants with explicit budget policy history.',
            '# TYPE customer_service_model_budget_policies gauge',
            f'customer_service_model_budget_policies {value["policies"]}',
            '# HELP customer_service_model_budget_reservations_total Durable model budget reservations.',
            '# TYPE customer_service_model_budget_reservations_total counter',
            f'customer_service_model_budget_reservations_total {value["reservations"]}',
            '# HELP customer_service_model_budget_soft_threshold_events_total Durable distinct threshold scopes observed.',
            '# TYPE customer_service_model_budget_soft_threshold_events_total counter',
        ]
        lines.extend(f'customer_service_model_budget_soft_threshold_events_total{{scope="{scope}"}} {value["soft"][scope]}'
                     for scope in ('tenant','day','run'))
        lines.extend([
            '# HELP customer_service_model_budget_rejections_total Durable terminal Runs rejected by budget control.',
            '# TYPE customer_service_model_budget_rejections_total counter',
        ])
        lines.extend(f'customer_service_model_budget_rejections_total{{reason="{reason}"}} {value["rejections"][reason]}'
                     for reason in ('exceeded','unavailable'))
        lines.extend([
            '# HELP customer_service_model_budget_unresolved_tenants Tenants with unreserved attempts lacking complete estimates.',
            '# TYPE customer_service_model_budget_unresolved_tenants gauge',
            f'customer_service_model_budget_unresolved_tenants {value["unresolved_tenants"]}',
        ])
        return lines
