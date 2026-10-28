"""Real receipt aggregation and privacy, synthetic local inference only."""
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
import uuid
import pytest
import sqlalchemy as sa
from app.local_inference_metrics import LocalInferenceMetrics
from app.http_observability import HttpTelemetry
from app.persistence.schema import metadata
from test_local_inference_usage import ledger,postgres_database


def test_real_metrics_count_known_unknown_and_stale_without_business_labels(ledger):
    bundle,token,meter=ledger
    with meter.measure('embedding_query',1,1024) as usage: usage.observe_tokens(17)
    attempt=meter.begin('rerank',1,1024)
    meter.finish(attempt,'error',None)
    pending=meter.begin('embedding_document',1,1024)
    # Seed an additional, explicitly synthetic old receipt; do not rewrite any
    # existing immutable evidence or change the database clock.
    table=metadata.tables['audit_logs']
    with bundle.engine.begin() as db:
        source=dict(db.execute(sa.select(table).where(table.c.id==uuid.UUID(pending))).mappings().one())
        new_id=uuid.uuid4()
        db.execute(table.insert().values(**{**source,'id':new_id,'entity_id':str(new_id),
            'created_at':datetime.now(timezone.utc)-timedelta(minutes=6)}))
    collector=LocalInferenceMetrics(bundle.engine)
    value=collector.snapshot()
    assert value['success']==1
    assert value['purposes']['embedding_query']['tokens']==17
    assert value['purposes']['embedding_query']['completed']==1
    assert value['purposes']['rerank']['failed']==1
    assert value['purposes']['rerank']['tokens']==0
    assert value['purposes']['rerank']['unknown_usage']==1
    assert value['purposes']['embedding_document']['unresolved']==2
    assert value['purposes']['embedding_document']['stale']==1
    telemetry=HttpTelemetry()
    telemetry.local_inference_metrics=collector
    output=telemetry.prometheus()
    assert 'customer_service_local_inference_collection_success 1' in output
    assert 'customer_service_local_inference_stale_unresolved{purpose="embedding_document"} 1' in output
    assert all(secret not in output for secret in (token,pending,'target_tenant_id','model_id','weights_sha256','synthetic'))


def test_real_app_assembles_private_collector_only_when_metrics_enabled(ledger,tmp_path):
    from app.main import create_app
    from app.private_metrics import MetricsPolicy
    from app.security import SecretStore
    bundle,_,_=ledger
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
        metrics_policy=MetricsPolicy(token=b'synthetic-inference-metrics-only-01234567890123456789'))
    assert isinstance(app.state.http_telemetry.local_inference_metrics,LocalInferenceMetrics)
    assert all(route.path!='/metrics' for route in app.routes)


def test_collection_failure_does_not_publish_zero_counts_or_raw_errors():
    def fail(): raise RuntimeError('synthetic-sensitive-connection')
    collector=LocalInferenceMetrics(SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),begin=fail))
    output='\n'.join(collector.prometheus())
    assert 'collection_success 0' in output
    assert 'attempts_total' not in output and 'unresolved{' not in output
    assert 'sensitive' not in output


def test_real_invalid_receipt_purpose_fails_evidence_collection(ledger):
    bundle,_,meter=ledger
    attempt=meter.begin('embedding_query',1,1024)
    table=metadata.tables['audit_logs']
    with bundle.engine.begin() as db:
        source=dict(db.execute(sa.select(table).where(table.c.id==uuid.UUID(attempt))).mappings().one())
        new_id=uuid.uuid4()
        db.execute(table.insert().values(**{**source,'id':new_id,'entity_id':str(new_id),
            'metadata_json':{**source['metadata_json'],'purpose':'synthetic-invalid'}}))
    value=LocalInferenceMetrics(bundle.engine).snapshot()
    assert value['success']==0 and value['purposes'] is None
