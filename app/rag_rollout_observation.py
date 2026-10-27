"""PG-derived candidate-only operational windows. Never release authority."""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
import uuid
import sqlalchemy as sa
from app.rag_rollout_plan import validate_plan
from app.persistence.schema import metadata

FIELDS=frozenset(('schema_version','scope','tenant_id','rollout_id','index_id','route_version','step_index',
    'plan_sha256','window_started_at','observed_at','expires_at','requests','completed','failed','pending',
    'unknown_cost_runs','unknown_terminal_cost_runs','invalid_duration_runs','invalid_context_runs','p95_ms','max_cost_per_request','currency'))


def collect_window(db,row):
    # One MVCC statement, latest append-only assessment per attempt. No raw
    # prompts, answers, session hashes or per-user rows leave this query.
    result=db.execute(sa.text("""
      WITH stamp AS (SELECT statement_timestamp() AS observed), scoped AS MATERIALIZED (
        SELECT r.*, (coalesce(r.rag_route->>'model_config_sha256'=i.body->'binding'->>'model_config_sha256',false)
          AND coalesce(r.rag_route->>'prompt_sha256'=i.body->'binding'->>'prompt_sha256',false)) AS context_valid
        FROM public.agent_runs r JOIN public.rag_release_intents i
          ON i.tenant_id=r.tenant_id AND i.release_id=:release
        WHERE r.tenant_id=:tenant AND r.rag_route IS NOT NULL AND r.rag_route->>'rollout_id'=:rollout
          AND r.rag_route->>'route_version'=:version
          AND r.rag_route->>'index_id'=i.candidate_index_id::text
      ), per_run AS (
        SELECT r.id,r.status,r.started_at,r.finished_at,r.context_valid,s.observed,
          count(a.attempt_id) AS attempts,
          bool_and(coalesce(a.state<>'started' AND a.finished_at<=s.observed
            AND c.status IN ('estimated','operator_reported') AND c.amount IS NOT NULL
            AND c.currency=:currency AND c.created_at<=s.observed,false)) AS known,
          sum(c.amount) AS cost
        FROM scoped r CROSS JOIN stamp s LEFT JOIN public.model_usage_attempts a
          ON a.tenant_id=r.tenant_id AND a.run_id=r.id
        LEFT JOIN LATERAL (SELECT c.* FROM public.model_cost_assessments c
          WHERE c.tenant_id=a.tenant_id AND c.attempt_id=a.attempt_id
          ORDER BY c.revision DESC LIMIT 1) c ON true
        GROUP BY r.id,r.status,r.started_at,r.finished_at,r.context_valid,s.observed
      ) SELECT (SELECT observed FROM stamp) AS observed_at,count(*) AS requests,
        count(*) FILTER (WHERE status='complete') AS completed,
        count(*) FILTER (WHERE status IN ('error','interrupted')) AS failed,
        count(*) FILTER (WHERE status IN ('queued','running')) AS pending,
        count(*) FILTER (WHERE attempts=0 OR known IS NOT TRUE) AS unknown_cost_runs,
        count(*) FILTER (WHERE status NOT IN ('queued','running') AND
          (attempts=0 OR known IS NOT TRUE)) AS unknown_terminal_cost_runs,
        count(*) FILTER (WHERE NOT context_valid) AS invalid_context_runs,
        count(*) FILTER (WHERE status NOT IN ('queued','running') AND
          (finished_at IS NULL OR finished_at<started_at OR finished_at>observed)) AS invalid_duration_runs,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at-started_at)*1000)
          FILTER (WHERE finished_at>=started_at AND finished_at<=observed) AS p95_ms,
        max(cost) FILTER (WHERE attempts>0 AND known IS TRUE) AS peak_cost
      FROM per_run
    """),{'release':row['release_id'],'tenant':row['tenant_id'],'rollout':str(row['rollout_id']),
           'version':str(row['version']),'currency':row['plan']['currency']}).mappings().one()
    release=metadata.tables['rag_release_intents']
    index=db.execute(sa.select(release.c.candidate_index_id).where(release.c.tenant_id==row['tenant_id'],
        release.c.release_id==row['release_id'])).scalar_one()
    counts={key:int(result[key]) for key in ('requests','completed','failed','pending','unknown_cost_runs','unknown_terminal_cost_runs','invalid_duration_runs','invalid_context_runs')}
    return {'schema_version':1,'scope':'postgres_candidate_operational_window','tenant_id':str(row['tenant_id']),
        'rollout_id':str(row['rollout_id']),'index_id':str(index),'route_version':row['version'],'step_index':row['step_index'],
        'plan_sha256':row['plan_sha256'],'window_started_at':row['step_started_at'].isoformat(),
        'observed_at':result['observed_at'].isoformat(),'expires_at':row['expires_at'].isoformat(),**counts,
        'p95_ms':format(result['p95_ms'],'f') if result['p95_ms'] is not None else None,
        'max_cost_per_request':format(result['peak_cost'],'f') if not counts['unknown_cost_runs'] and result['peak_cost'] is not None else None,
        'currency':row['plan']['currency']}


def assess_window(plan,report,*,now):
    validate_plan(plan)
    if not isinstance(report,dict) or set(report)!=FIELDS or type(report['schema_version']) is not int or report['schema_version']!=1:
        raise ValueError('Invalid operational observation fields')
    if report['scope']!='postgres_candidate_operational_window':
        raise ValueError('Not an operational observation')
    for key in ('tenant_id','rollout_id','index_id'):
        if not isinstance(report[key],str) or str(uuid.UUID(report[key]))!=report[key]:
            raise ValueError('Invalid observation identity')
    if not isinstance(report['plan_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',report['plan_sha256']):
        raise ValueError('Invalid observation plan digest')
    counts=('requests','completed','failed','pending','unknown_cost_runs','unknown_terminal_cost_runs','invalid_duration_runs','invalid_context_runs')
    for key in (*counts,'route_version','step_index'):
        if type(report[key]) is not int or not 0<=report[key]<=1000000000:
            raise ValueError('Invalid observation count or version')
    n=report['requests']
    if (report['completed']+report['failed']+report['pending']!=n
            or report['unknown_cost_runs']>n or report['invalid_duration_runs']>n
            or report['unknown_terminal_cost_runs']>min(report['unknown_cost_runs'],n-report['pending'])
            or report['unknown_cost_runs']-report['unknown_terminal_cost_runs']>report['pending']
            or report['invalid_context_runs']>n
            or report['step_index']>=len(plan['steps_bps'])):
        raise ValueError('Inconsistent observation counts')
    def stamp(key):
        if not isinstance(report[key],str) or len(report[key])>64:
            raise ValueError('Invalid observation timestamp')
        value=datetime.fromisoformat(report[key].replace('Z','+00:00'))
        if value.tzinfo is None or value.utcoffset().total_seconds()!=0:
            raise ValueError('UTC observation time required')
        return value
    start,observed,expires=(stamp(key) for key in ('window_started_at','observed_at','expires_at'))
    if not isinstance(now,datetime) or now.tzinfo is None or start>observed or observed>now:
        raise ValueError('Invalid observation clock ordering')
    def number(key):
        value=report[key]
        if value is None:
            return None
        if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,28}(?:\.[0-9]{1,12})?',value):
            raise ValueError('Invalid operational decimal')
        return Decimal(value)
    p95,cost=number('p95_ms'),number('max_cost_per_request')
    reasons=[]
    plan_digest=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if report['plan_sha256']!=plan_digest: reasons.append('plan_mismatch')
    if (observed-start).total_seconds()<plan['min_observation_seconds']: reasons.append('window_too_short')
    if (now-observed).total_seconds()>plan['max_observation_age_seconds']: reasons.append('stale_observation')
    if now>=expires: reasons.append('rollout_expired')
    if n<plan['min_requests']: reasons.append('insufficient_requests')
    if report['pending']: reasons.append('pending_runs')
    if report['invalid_context_runs']: reasons.append('context_mismatch')
    if report['unknown_cost_runs'] or cost is None: reasons.append('unknown_cost')
    if report['invalid_duration_runs'] or p95 is None: reasons.append('invalid_latency')
    rate=Decimal(plan['max_error_rate'])
    numerator,denominator=rate.as_integer_ratio()
    if report['failed']*denominator>n*numerator: reasons.append('error_rate_exceeded')
    if p95 is not None and p95>Decimal(plan['max_p95_ms']): reasons.append('p95_exceeded')
    if cost is not None and cost>Decimal(plan['max_cost_per_request']): reasons.append('cost_exceeded')
    if report['currency']!=plan['currency']: reasons.append('currency_mismatch')
    digest=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'thresholds_satisfied':not reasons,'reason_codes':reasons,'observation_sha256':digest,
        'scope':'operational_thresholds_not_quality_review_or_release_authority'}


def assess_health(plan,report,*,now):
    """Classify a verified window; this does not change traffic or authorize release.

    Pending work is expected during live traffic. Only terminal unknown costs
    are an integrity failure; rate/latency/cost thresholds wait for a mature,
    settled window so startup noise cannot trigger an automatic stop.
    """
    assessment=assess_window(plan,report,now=now)
    reasons=set(assessment['reason_codes'])
    fatal=reasons & {'plan_mismatch','stale_observation','rollout_expired',
        'context_mismatch','currency_mismatch'}
    if report['unknown_terminal_cost_runs']:
        fatal.add('unknown_terminal_cost')
    if report['invalid_duration_runs']:
        fatal.add('invalid_terminal_duration')
    waiting=reasons & {'window_too_short','insufficient_requests','pending_runs'}
    if not waiting:
        fatal.update(reasons & {'unknown_cost','invalid_latency','error_rate_exceeded',
            'p95_exceeded','cost_exceeded'})
    return {'decision':'pause' if fatal else 'hold' if waiting else 'healthy',
        'reason_codes':sorted(fatal or waiting),'observation_sha256':assessment['observation_sha256'],
        'scope':'health_classification_not_traffic_control'}
