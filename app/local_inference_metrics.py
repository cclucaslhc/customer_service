"""Read-only, content-free aggregates from local inference audit receipts."""
from time import perf_counter
import sqlalchemy as sa
from app.local_inference_usage import PURPOSES
from app.metrics_export import MetricsRenderLimit

SUMMARY=sa.text("""
WITH finals AS (
 SELECT tenant_id,entity_id,count(*) n,(jsonb_agg(metadata_json)->0) d
 FROM public.audit_logs WHERE action='local_inference.finished'
 GROUP BY tenant_id,entity_id
)
SELECT s.metadata_json->>'purpose' purpose,count(*) attempts,
 count(*) FILTER (WHERE f.n IS NULL) unresolved,
 count(*) FILTER (WHERE f.n IS NULL AND s.created_at<clock_timestamp()-interval '5 minutes') stale,
 count(*) FILTER (WHERE f.d->>'state'='completed') completed,
 count(*) FILTER (WHERE f.d->>'state' IN ('error','cancelled','incomplete')) failed,
 count(*) FILTER (WHERE f.n=1 AND f.d->>'input_tokens' IS NULL) unknown_usage,
 coalesce(sum(CASE WHEN f.d->>'input_tokens' IS NOT NULL THEN (f.d->>'input_tokens')::bigint ELSE 0 END),0) tokens,
 count(*) FILTER (WHERE s.entity_type<>'inference_attempt' OR s.entity_id<>s.id::text
   OR s.metadata_json->>'schema_version' IS DISTINCT FROM '1'
   OR s.metadata_json->>'provider' IS DISTINCT FROM 'local'
   OR s.metadata_json->>'api_billing' IS DISTINCT FROM 'not_applicable_offline_v1'
   OR (f.n IS NOT NULL AND (f.n<>1 OR f.d->>'state' NOT IN ('completed','error','cancelled','incomplete')
     OR f.d->>'state' IS NULL
     OR (f.d-ARRAY['state','input_tokens','usage_status']) IS DISTINCT FROM
        (s.metadata_json-ARRAY['input_tokens'])
     OR (f.d->>'input_tokens' IS NOT NULL AND f.d->>'usage_status' IS DISTINCT FROM 'tokenizer_observed')
     OR (f.d->>'input_tokens' IS NULL AND f.d->>'usage_status' IS DISTINCT FROM 'unknown')
     OR (f.d->>'input_tokens' IS NOT NULL AND ((f.d->>'input_tokens')::bigint<=0
          OR (f.d->>'input_tokens')::bigint>(s.metadata_json->>'max_input_tokens')::bigint))))) invalid
FROM public.audit_logs s LEFT JOIN finals f ON f.tenant_id=s.tenant_id AND f.entity_id=s.id::text
WHERE s.action='local_inference.started' GROUP BY s.metadata_json->>'purpose'
""")
ORPHANS=sa.text("""SELECT EXISTS(SELECT 1 FROM public.audit_logs f
 WHERE f.action='local_inference.finished' AND NOT EXISTS(SELECT 1 FROM public.audit_logs s
 WHERE s.id::text=f.entity_id AND s.tenant_id=f.tenant_id AND s.action='local_inference.started'))""")


class LocalInferenceMetrics:
    def __init__(self,engine):
        if engine.dialect.name!='postgresql': raise ValueError('Local inference metrics require PostgreSQL')
        self.engine=engine

    @staticmethod
    def _check(deadline,cancel_event):
        if cancel_event is not None and cancel_event.is_set(): raise MetricsRenderLimit('cancelled')
        if deadline is not None and perf_counter()>=deadline: raise MetricsRenderLimit('deadline')

    def snapshot(self,*,deadline=None,cancel_event=None):
        self._check(deadline,cancel_event)
        try:
            with self.engine.begin() as db:
                db.execute(sa.text('SET TRANSACTION READ ONLY'))
                db.execute(sa.text("SET LOCAL statement_timeout='750ms'"))
                db.execute(sa.text("SET LOCAL lock_timeout='100ms'"))
                collected=float(db.execute(sa.text("SELECT extract(epoch FROM clock_timestamp())")).scalar_one())
                rows=db.execute(SUMMARY).mappings().all()
                if db.execute(ORPHANS).scalar_one(): raise ValueError('Orphan inference receipt')
                values={purpose:dict.fromkeys(('attempts','unresolved','stale','completed','failed','tokens','unknown_usage'),0) for purpose in PURPOSES}
                for row in rows:
                    if row['purpose'] not in PURPOSES or row['invalid']: raise ValueError('Invalid inference receipts')
                    values[row['purpose']]={key:int(row[key]) for key in values[row['purpose']]}
                self._check(deadline,cancel_event)
                return {'success':1,'collected_at':collected,'purposes':values}
        except MetricsRenderLimit: raise
        except Exception:
            # No SQL, parameters, tenant IDs, model names or exception text.
            return {'success':0,'collected_at':0.,'purposes':None}

    def prometheus(self,*,deadline=None,cancel_event=None):
        value=self.snapshot(deadline=deadline,cancel_event=cancel_event)
        prefix='customer_service_local_inference_'
        lines=[f'# TYPE {prefix}collection_success gauge',f'{prefix}collection_success {value["success"]}',
            f'# TYPE {prefix}collection_timestamp_seconds gauge',f'{prefix}collection_timestamp_seconds {value["collected_at"]}']
        if not value['success']: return lines # Omit unknown counts; never manufacture healthy zeroes.
        for field,suffix,kind in (
            ('attempts','attempts_total','counter'),('completed','completed_total','counter'),
            ('failed','failed_total','counter'),('tokens','tokenized_input_tokens_total','counter'),
            ('unknown_usage','unknown_token_usage_total','counter'),
            ('unresolved','unresolved','gauge'),('stale','stale_unresolved','gauge')):
            lines.append(f'# TYPE {prefix}{suffix} {kind}')
            for purpose in sorted(PURPOSES):
                lines.append(f'{prefix}{suffix}{{purpose="{purpose}"}} {value["purposes"][purpose][field]}')
        return lines
