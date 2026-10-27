from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import pytest
from app.rag_rollout_observation import assess_window,assess_health
from test_rag_rollout_plan import plan

NOW=datetime(2026,10,26,0,0,tzinfo=timezone.utc)


def report():
    policy=plan()
    return {'schema_version':1,'scope':'postgres_candidate_operational_window',
        'tenant_id':'11111111-1111-4111-8111-111111111111',
        'rollout_id':'22222222-2222-4222-8222-222222222222',
        'index_id':'33333333-3333-4333-8333-333333333333',
        'route_version':1,'step_index':0,
        'plan_sha256':hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'window_started_at':(NOW-timedelta(seconds=300)).isoformat(),'observed_at':NOW.isoformat(),
        'expires_at':(NOW+timedelta(seconds=600)).isoformat(),'requests':100,'completed':99,'failed':1,
        'pending':0,'unknown_cost_runs':0,'unknown_terminal_cost_runs':0,'invalid_duration_runs':0,'invalid_context_runs':0,
        'p95_ms':'10000','max_cost_per_request':'0.1','currency':'CNY'}


def test_exact_operational_thresholds_are_not_release_authorization():
    result=assess_window(plan(),report(),now=NOW)
    assert result['thresholds_satisfied'] and result['reason_codes']==[]
    assert result['scope']=='operational_thresholds_not_quality_review_or_release_authority'


@pytest.mark.parametrize('changes,reason',[
    ({'completed':98,'failed':2},'error_rate_exceeded'),
    ({'completed':98,'pending':1},'pending_runs'),
    ({'unknown_cost_runs':1,'unknown_terminal_cost_runs':1,'max_cost_per_request':None},'unknown_cost'),
    ({'invalid_duration_runs':1},'invalid_latency'),
    ({'invalid_context_runs':1},'context_mismatch'),
    ({'p95_ms':'10000.000001'},'p95_exceeded'),
    ({'max_cost_per_request':'0.100000000001'},'cost_exceeded'),
    ({'currency':'USD'},'currency_mismatch'),({'plan_sha256':'b'*64},'plan_mismatch'),
    ({'window_started_at':(NOW-timedelta(seconds=299)).isoformat()},'window_too_short'),
    ({'observed_at':(NOW-timedelta(seconds=61)).isoformat()},'stale_observation'),
    ({'expires_at':NOW.isoformat()},'rollout_expired'),
    ({'requests':99,'completed':98},'insufficient_requests')])
def test_operational_breaches_fail_closed(changes,reason):
    value=report()
    value.update(changes)
    assert reason in assess_window(plan(),value,now=NOW)['reason_codes']


@pytest.mark.parametrize('changes',[
    {'requests':True},{'unknown_cost_runs':101},{'completed':100},
    {'max_cost_per_request':'NaN'},{'p95_ms':1.0},{'p95_ms':'1e1000'},
    {'observed_at':(NOW+timedelta(seconds=1)).isoformat()},
    {'window_started_at':True},{'scope':'user_submitted_metrics'},
    {'step_index':16},{'extra':'approved'}])
def test_malformed_reports_cannot_become_success(changes):
    value=deepcopy(report())
    value.update(changes)
    with pytest.raises(ValueError):
        assess_window(plan(),value,now=NOW)


@pytest.mark.parametrize('changes,decision,reason',[
    ({},'healthy',None),
    ({'completed':98,'pending':1,'unknown_cost_runs':1,'max_cost_per_request':None},'hold','pending_runs'),
    ({'requests':1,'completed':0,'failed':1},'hold','insufficient_requests'),
    ({'completed':98,'failed':2},'pause','error_rate_exceeded'),
    ({'unknown_cost_runs':1,'unknown_terminal_cost_runs':1,'max_cost_per_request':None},'pause','unknown_terminal_cost'),
    ({'completed':98,'pending':1,'unknown_cost_runs':2,'unknown_terminal_cost_runs':1,
      'max_cost_per_request':None},'pause','unknown_terminal_cost'),
    ({'completed':98,'pending':1,'invalid_context_runs':1},'pause','context_mismatch'),
    ({'invalid_duration_runs':1},'pause','invalid_terminal_duration'),
])
def test_health_distinguishes_live_work_from_terminal_failures(changes,decision,reason):
    value=report()
    value.update(changes)
    result=assess_health(plan(),value,now=NOW)
    assert result['decision']==decision
    if reason:
        assert reason in result['reason_codes']


@pytest.mark.parametrize('changes',[
    {'unknown_terminal_cost_runs':1},
    {'unknown_cost_runs':1},
    {'completed':0,'failed':0,'pending':100,'unknown_cost_runs':1,'unknown_terminal_cost_runs':1},
])
def test_terminal_cost_counts_must_be_consistent(changes):
    value=report()
    value.update(changes)
    with pytest.raises(ValueError):
        assess_health(plan(),value,now=NOW)
