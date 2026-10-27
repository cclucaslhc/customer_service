"""Private, retrieval-only blind-set scorer with no question/result text output.

The caller supplies an authenticated, role-bound search adapter. This scorer
does not provision identities, call online models, or judge generated facts.
"""

import hashlib
import json
import math
import time
from pathlib import Path

from app.rag_blind_set import validate_blind_set


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 2)


def evaluate_retrieval(path: str | Path, search, *, minimum: int = 200) -> dict:
    """Score scoped Top-3 retrieval; never return questions or source identities.

    `search(tenant_alias, role, question, limit=3)` must use the corresponding
    authenticated identity and return at most three rows. Each row must expose
    the same opaque tenant/document references as the approved mapping plus
    document version and chunk SHA-256. The adapter must be independently
    audited; these claimed fields alone do not prove authorization.
    """
    source = Path(path)
    frozen = validate_blind_set(source, minimum=minimum)
    digest = hashlib.sha256()
    count = positives = positives_hit = expected_total = expected_hit = 0
    empty = negative_empty = negatives = cross_tenant = 0
    latencies: list[float] = []
    categories: dict[str, dict[str, int]] = {}
    with source.open('rb') as stream:
        for raw in stream:
            digest.update(raw)
            case = json.loads(raw)
            started = time.perf_counter()
            rows = search(case['tenant_alias'], case['role'], case['question'], limit=3)
            latencies.append((time.perf_counter() - started) * 1000)
            if not isinstance(rows, list) or len(rows) > 3:
                raise ValueError('Blind retrieval adapter returned an invalid Top-3 result')
            identities = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {
                        'tenant_alias', 'document_ref', 'version', 'content_sha256'}:
                    raise ValueError('Blind retrieval adapter returned invalid source identity')
                if (not isinstance(row['tenant_alias'], str) or
                        not isinstance(row['document_ref'], str) or
                        type(row['version']) is not int or row['version'] < 1 or
                        not isinstance(row['content_sha256'], str) or
                        len(row['content_sha256']) != 64):
                    raise ValueError('Blind retrieval adapter returned invalid source identity')
                if row['tenant_alias'] != case['tenant_alias']:
                    cross_tenant += 1
                else:
                    identities.add((row['document_ref'], row['version'], row['content_sha256']))
            count += 1
            empty += not rows
            if case['answerable']:
                positives += 1
                expected = {(item['document_ref'], item['version'], item['content_sha256'])
                            for item in case['expected_sources']}
                hits = len(expected & identities)
                expected_total += len(expected)
                expected_hit += hits
                positives_hit += hits > 0
            else:
                negatives += 1
                negative_empty += not rows
            for category in set(case['category']):
                group = categories.setdefault(category, {'cases': 0, 'answerable': 0,
                                                          'answerable_hit': 0})
                group['cases'] += 1
                group['answerable'] += bool(case['answerable'])
                group['answerable_hit'] += bool(case['answerable'] and hits > 0)
    if digest.hexdigest() != frozen['sha256']:
        raise ValueError('Blind set changed during retrieval evaluation')
    return {'dataset_sha256': frozen['sha256'], 'cases': count,
            'answerable_cases': positives, 'unanswerable_cases': negatives,
            'answerable_case_hit_at_3': positives_hit / positives if positives else None,
            'expected_source_recall_at_3': expected_hit / expected_total if expected_total else None,
            'empty_results': empty, 'unanswerable_empty_results': negative_empty,
            'cross_tenant_results': cross_tenant,
            'latency_ms': {'p50': _percentile(latencies, 0.5),
                           'p95': _percentile(latencies, 0.95)},
            'categories': dict(sorted(categories.items())),
            'scope': 'retrieval_only_not_generation_or_fact_support'}
