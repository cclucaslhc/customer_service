"""Fail-closed threshold assessment, NOT business approval or release authority.

An eventual rollout controller must independently authenticate reviewers,
freeze/sign provenance and persist approval. Never authorize from this result
alone: structural reviewer references do not prove independent human review.
"""
import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation


BINDING = frozenset(('tenant_id','index_id','manifest_sha256','dataset_sha256',
    'mapping_sha256','model_config_sha256','prompt_sha256'))
POLICY = frozenset(('schema_version','min_cases','min_supported_rate','min_refusal_rate',
    'max_p95_ms','max_cost','currency','max_age_seconds'))
EVIDENCE = BINDING | frozenset(('schema_version','scope','evaluation_id','completed_at',
    'reviewer_refs','cases','answerable','unanswerable','supported_answers',
    'correct_refusals','leaked_cases','p95_ms','cost','currency'))


def _shape(value, fields):
    if not isinstance(value,dict) or set(value)!=fields:
        raise ValueError('Invalid release assessment fields')


def _integer(value, minimum=0, maximum=1000000):
    if type(value) is not int or not minimum<=value<=maximum:
        raise ValueError('Invalid release assessment count or bound')
    return value


def _decimal(value, *, maximum=Decimal('1000000000000000000')):
    if not isinstance(value,str) or not 1<=len(value)<=64:
        raise ValueError('Assessment decimals must be explicit strings')
    try:
        number=Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid release assessment decimal') from None
    if not number.is_finite() or number<0 or number>maximum:
        raise ValueError('Invalid release assessment decimal')
    parts=number.as_tuple()
    if not -12<=parts.exponent<=18 or len(parts.digits)>31:
        raise ValueError('Assessment decimal precision exceeds safe bounds')
    return number


def _uuid(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:
        raise ValueError('Assessment identities must be canonical UUIDs')


def _binding(value):
    _shape(value,BINDING)
    for key,item in value.items():
        if key in {'tenant_id','index_id'}:
            _uuid(item)
        elif (not isinstance(item,str) or len(item)!=64
              or any(char not in '0123456789abcdef' for char in item)):
            raise ValueError('Invalid assessment binding hash')


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest()


def assess_quality(policy, evidence, expected_binding, *, now):
    """Assess explicit thresholds against one pinned independently reviewed run.

    Retrieval-only scorer output is deliberately incompatible. No threshold
    defaults, model calls, file reads, approval flags, index changes or auto
    rollback occur here. Count metrics concern whole reviewed cases, not an
    automatic claim that citations semantically support the generated answer.
    """
    _shape(policy,POLICY)
    _shape(evidence,EVIDENCE)
    _binding(expected_binding)
    _binding({key:evidence[key] for key in BINDING})
    if type(policy['schema_version']) is not int or policy['schema_version']!=1:
        raise ValueError('Unsupported threshold policy version')
    if type(evidence['schema_version']) is not int or evidence['schema_version']!=1:
        raise ValueError('Unsupported assessment evidence version')
    if evidence['scope']!='independently_reviewed_generation':
        raise ValueError('Generation fact review evidence is required, not retrieval-only scores')
    _uuid(evidence['evaluation_id'])
    refs=evidence['reviewer_refs']
    if not isinstance(refs,list) or not 2<=len(refs)<=10:
        raise ValueError('At least two distinct reviewer references are required')
    for ref in refs:
        _uuid(ref)
    if len(set(refs))!=len(refs):
        raise ValueError('Reviewer references must be distinct')
    if policy['currency'] not in ('CNY','USD') or evidence['currency'] not in ('CNY','USD'):
        raise ValueError('Unsupported assessment currency')
    min_cases=_integer(policy['min_cases'],200)
    age_limit=_integer(policy['max_age_seconds'],1,604800)
    support=_decimal(policy['min_supported_rate'],maximum=Decimal(1))
    refusal=_decimal(policy['min_refusal_rate'],maximum=Decimal(1))
    if support==0 or refusal==0:
        raise ValueError('Quality rate thresholds must be positive')
    p95_limit=_decimal(policy['max_p95_ms'])
    cost_limit=_decimal(policy['max_cost'])
    p95=_decimal(evidence['p95_ms'])
    cost=_decimal(evidence['cost'])
    counts={key:_integer(evidence[key]) for key in ('cases','answerable','unanswerable',
        'supported_answers','correct_refusals','leaked_cases')}
    if (counts['answerable']+counts['unanswerable']!=counts['cases']
            or counts['supported_answers']>counts['answerable']
            or counts['correct_refusals']>counts['unanswerable']
            or counts['leaked_cases']>counts['cases']):
        raise ValueError('Inconsistent reviewed case counts')
    stamp=evidence['completed_at']
    if not isinstance(stamp,str) or len(stamp)>64:
        raise ValueError('Assessment completion requires a UTC timestamp')
    try:
        completed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    except ValueError:
        raise ValueError('Invalid assessment completion timestamp') from None
    if (completed.tzinfo is None or completed.utcoffset().total_seconds()!=0
            or not isinstance(now,datetime) or now.tzinfo is None
            or now.utcoffset().total_seconds()!=0):
        raise ValueError('Assessment clock and completion must use UTC')
    age=(now-completed).total_seconds()
    reasons=[]
    def reject(condition,code):
        if condition: reasons.append(code)
    reject(any(evidence[key]!=expected_binding[key] for key in BINDING),'binding_mismatch')
    reject(age<0,'future_evidence')
    reject(age>age_limit,'stale_evidence')
    reject(counts['cases']<min_cases,'insufficient_cases')
    reject(not counts['answerable'],'no_answerable_cases')
    reject(not counts['unanswerable'],'no_unanswerable_cases')
    numerator,denominator=support.as_integer_ratio()
    reject(counts['supported_answers']*denominator<numerator*counts['answerable'],'support_below_threshold')
    numerator,denominator=refusal.as_integer_ratio()
    reject(counts['correct_refusals']*denominator<numerator*counts['unanswerable'],'refusal_below_threshold')
    reject(counts['leaked_cases']!=0,'cross_tenant_leak')
    reject(p95>p95_limit,'p95_exceeded')
    reject(evidence['currency']!=policy['currency'],'currency_mismatch')
    reject(cost>cost_limit,'cost_exceeded')
    return {'thresholds_satisfied':not reasons,'reason_codes':reasons,
        'policy_sha256':_hash(policy),'evidence_sha256':_hash(evidence),
        'binding_sha256':_hash(expected_binding),
        'scope':'threshold_assessment_not_release_authorization'}
