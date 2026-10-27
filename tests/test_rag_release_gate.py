from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from app.rag_release_gate import assess_quality, BINDING


NOW=datetime(2026,10,25,0,0,tzinfo=timezone.utc)


def sample():
    binding={'tenant_id':'11111111-1111-4111-8111-111111111111',
        'index_id':'22222222-2222-4222-8222-222222222222',
        **{key:'a'*64 for key in BINDING if key.endswith('sha256')}}
    policy={'schema_version':1,'min_cases':200,'min_supported_rate':'0.95',
        'min_refusal_rate':'0.90','max_p95_ms':'1000','max_cost':'5',
        'currency':'CNY','max_age_seconds':3600}
    evidence={**binding,'schema_version':1,'scope':'independently_reviewed_generation',
        'evaluation_id':'33333333-3333-4333-8333-333333333333',
        'completed_at':NOW.isoformat(),'reviewer_refs':[
            '44444444-4444-4444-8444-444444444444','55555555-5555-4555-8555-555555555555'],
        'cases':200,'answerable':100,'unanswerable':100,'supported_answers':95,
        'correct_refusals':90,'leaked_cases':0,'p95_ms':'1000','cost':'5','currency':'CNY'}
    return policy,evidence,binding


def test_exact_thresholds_are_satisfied_but_never_claim_approval():
    policy,evidence,binding=sample()
    before=deepcopy((policy,evidence,binding))
    result=assess_quality(policy,evidence,binding,now=NOW)
    assert result['thresholds_satisfied'] and result['reason_codes']==[]
    assert result['scope']=='threshold_assessment_not_release_authorization'
    assert set(result)=={'thresholds_satisfied','reason_codes','scope',
        'policy_sha256','evidence_sha256','binding_sha256'}
    assert all(len(result[key])==64 for key in result if key.endswith('sha256'))
    assert (policy,evidence,binding)==before


@pytest.mark.parametrize('key',sorted(BINDING))
def test_every_frozen_context_binding_must_match(key):
    policy,evidence,binding=sample()
    evidence[key]='66666666-6666-4666-8666-666666666666' if key.endswith('_id') else 'b'*64
    result=assess_quality(policy,evidence,binding,now=NOW)
    assert not result['thresholds_satisfied'] and 'binding_mismatch' in result['reason_codes']


@pytest.mark.parametrize('field,value,reason',[
    ('supported_answers',94,'support_below_threshold'),
    ('correct_refusals',89,'refusal_below_threshold'),
    ('leaked_cases',1,'cross_tenant_leak'),('p95_ms','1000.000001','p95_exceeded'),
    ('cost','5.000001','cost_exceeded'),('currency','USD','currency_mismatch')])
def test_policy_breaches_cannot_pass(field,value,reason):
    policy,evidence,binding=sample()
    evidence[field]=value
    result=assess_quality(policy,evidence,binding,now=NOW)
    assert not result['thresholds_satisfied'] and reason in result['reason_codes']


def test_decimal_rate_comparison_does_not_round_an_above_boundary_requirement_down():
    policy,evidence,binding=sample()
    policy['min_supported_rate']='0.950000000001'
    assert 'support_below_threshold' in assess_quality(policy,evidence,binding,now=NOW)['reason_codes']


@pytest.mark.parametrize('offset,reason',[(3601,'stale_evidence'),(-1,'future_evidence')])
def test_time_bounds(offset,reason):
    policy,evidence,binding=sample()
    evidence['completed_at']=(NOW-timedelta(seconds=offset)).isoformat()
    assert reason in assess_quality(policy,evidence,binding,now=NOW)['reason_codes']


@pytest.mark.parametrize('field,value',[
    ('scope','retrieval_only_not_generation_or_fact_support'),('schema_version',True),
    ('reviewer_refs',[]),('reviewer_refs',['44444444-4444-4444-8444-444444444444']*2),
    ('cases',True),('supported_answers',101),('completed_at','2026-10-25T00:00:00'),
    ('p95_ms','NaN'),('cost','Infinity'),('cost','-1'),('cost','1e-999999999')])
def test_invalid_evidence_is_rejected_not_converted_to_zero_or_success(field,value):
    policy,evidence,binding=sample()
    evidence[field]=value
    with pytest.raises(ValueError):
        assess_quality(policy,evidence,binding,now=NOW)


def test_boolean_approval_flag_is_not_a_release_contract():
    policy,evidence,binding=sample()
    evidence['approved']=True
    with pytest.raises(ValueError):
        assess_quality(policy,evidence,binding,now=NOW)


def test_missing_strata_and_small_samples_cannot_pass():
    policy,evidence,binding=sample()
    evidence.update(cases=199,answerable=199,unanswerable=0,supported_answers=199,correct_refusals=0)
    result=assess_quality(policy,evidence,binding,now=NOW)
    assert {'insufficient_cases','no_unanswerable_cases'}<=set(result['reason_codes'])


def test_canonical_receipts_bind_policy_and_evidence_without_depending_on_object_key_order():
    policy,evidence,binding=sample()
    initial=assess_quality(policy,evidence,binding,now=NOW)
    reordered=assess_quality(dict(reversed(list(policy.items()))),
        dict(reversed(list(evidence.items()))),dict(reversed(list(binding.items()))),now=NOW)
    assert reordered==initial
    evidence['cost']='4.9'
    changed=assess_quality(policy,evidence,binding,now=NOW)
    assert changed['evidence_sha256']!=initial['evidence_sha256']
    assert changed['policy_sha256']==initial['policy_sha256']
    assert changed['binding_sha256']==initial['binding_sha256']
    policy['max_cost']='6'
    assert assess_quality(policy,evidence,binding,now=NOW)['policy_sha256']!=initial['policy_sha256']


@pytest.mark.parametrize('field,value',[
    ('min_cases',199),('min_cases',True),('max_age_seconds',0),('min_supported_rate','0'),
    ('min_refusal_rate','1.1'),('max_cost','NaN'),('currency','unknown')])
def test_invalid_or_missing_policy_is_not_replaced_with_permissive_defaults(field,value):
    policy,evidence,binding=sample()
    policy[field]=value
    with pytest.raises(ValueError):
        assess_quality(policy,evidence,binding,now=NOW)
    policy.pop(field)
    with pytest.raises(ValueError):
        assess_quality(policy,evidence,binding,now=NOW)
