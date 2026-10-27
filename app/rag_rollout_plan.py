"""Strict approval-bound rollout plan. No routing or metric-source authority."""
from decimal import Decimal, InvalidOperation
import json
import re
import uuid


FIELDS = frozenset(('schema_version','steps_bps','cohort_salt','min_observation_seconds',
    'min_requests','max_error_rate','max_p95_ms','max_cost_per_request','currency',
    'max_observation_age_seconds'))


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != FIELDS or type(plan['schema_version']) is not int or plan['schema_version'] != 1:
        raise ValueError('Invalid rollout plan fields or schema')
    steps = plan['steps_bps']
    if (not isinstance(steps, list) or not 2 <= len(steps) <= 16
            or any(type(step) is not int or not 1 <= step <= 10000 for step in steps)
            or steps[-1] != 10000 or any(a >= b for a, b in zip(steps, steps[1:]))):
        raise ValueError('Rollout steps must strictly increase to 10000 basis points')
    for key, maximum in (('min_observation_seconds',86400),('min_requests',1000000),
                         ('max_observation_age_seconds',86400)):
        if type(plan[key]) is not int or not 1 <= plan[key] <= maximum:
            raise ValueError('Invalid explicit rollout observation bound')
    for key, maximum, positive in (('max_error_rate',Decimal(1),False),
                                  ('max_p95_ms',Decimal(3600000),True),
                                  ('max_cost_per_request',Decimal(1000000),False)):
        value = plan[key]
        if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,12})?',value):
            raise ValueError('Rollout decimals require bounded plain strings')
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError('Invalid rollout decimal') from None
        if not number.is_finite() or number < 0 or number > maximum or (positive and number == 0):
            raise ValueError('Rollout decimal outside allowed range')
    if plan['currency'] not in ('CNY','USD'):
        raise ValueError('Explicit supported rollout currency required')
    salt = plan['cohort_salt']
    if not isinstance(salt,str) or str(uuid.UUID(salt)) != salt:
        raise ValueError('Canonical rollout cohort salt required')
    return json.loads(json.dumps(plan,allow_nan=False))
