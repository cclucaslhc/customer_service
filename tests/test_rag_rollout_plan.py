from copy import deepcopy
import pytest
from app.rag_rollout_plan import validate_plan


def plan():
    return dict(schema_version=1, steps_bps=[100,500,2500,10000],
        cohort_salt='11111111-1111-4111-8111-111111111111',min_observation_seconds=300,
        min_requests=100,max_error_rate='0.01',max_p95_ms='10000',
        max_cost_per_request='0.1',currency='CNY',max_observation_age_seconds=60)


def test_explicit_plan_is_copied_not_mutable_after_validation():
    original=plan()
    frozen=validate_plan(original)
    original['steps_bps'][0]=200
    assert frozen['steps_bps'][0]==100


@pytest.mark.parametrize('key,value', [
    ('schema_version',True),('steps_bps',[10000]),('steps_bps',[100,100,10000]),
    ('steps_bps',[True,10000]),('steps_bps',[500,100]),('steps_bps',[100,9999]),
    ('max_error_rate','NaN'),('max_error_rate','1.01'),('max_error_rate',.01),
    ('max_p95_ms','0'),('max_cost_per_request','-1'),('max_cost_per_request','1e3'),
    ('min_requests',True),('min_observation_seconds',0),('max_observation_age_seconds',86401),
    ('currency',''),('cohort_salt','not-a-uuid')])
def test_invalid_plan_is_not_silently_defaulted(key,value):
    candidate=deepcopy(plan())
    candidate[key]=value
    with pytest.raises(ValueError):
        validate_plan(candidate)
