"""Structural gate for an independently approved RAG blind set.

This does not decide whether a question is de-identified or factually correct.
It intentionally returns only counts and opaque case IDs, never question text.
"""

import hashlib
import json
from pathlib import Path


REQUIRED = frozenset({
    'case_id', 'tenant_alias', 'role', 'question', 'answerable', 'category',
    'expected_facts', 'forbidden_claims', 'expected_sources',
})


def validate_blind_set(path: str | Path, *, minimum: int = 200) -> dict:
    if minimum < 1:
        raise ValueError('Invalid minimum case count')
    source = Path(path)
    digest = hashlib.sha256()
    seen = set()
    categories: dict[str, int] = {}
    answerable = 0
    with source.open('rb') as stream:
        for line_number, raw in enumerate(stream, 1):
            digest.update(raw)
            if len(raw) > 65536:
                raise ValueError(f'Blind-set line {line_number} exceeds 64 KiB')
            try:
                item = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f'Invalid blind-set JSON at line {line_number}') from exc
            if not isinstance(item, dict) or set(item) != REQUIRED:
                raise ValueError(f'Invalid blind-set fields at line {line_number}')
            case_id = item['case_id']
            if not isinstance(case_id, str) or not 1 <= len(case_id) <= 80 or case_id in seen:
                raise ValueError(f'Invalid or duplicate case ID at line {line_number}')
            seen.add(case_id)
            for field in ('tenant_alias', 'role', 'question'):
                if not isinstance(item[field], str) or not item[field].strip():
                    raise ValueError(f'Invalid {field} at line {line_number}')
            if type(item['answerable']) is not bool:
                raise ValueError(f'Invalid answerable at line {line_number}')
            answerable += item['answerable']
            for field in ('category', 'expected_facts', 'forbidden_claims'):
                values = item[field]
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError(f'Invalid {field} at line {line_number}')
            if not item['category'] or (item['answerable'] and not item['expected_facts']):
                raise ValueError(f'Missing blind-set labels at line {line_number}')
            for category in set(item['category']):
                categories[category] = categories.get(category, 0) + 1
            sources = item['expected_sources']
            if not isinstance(sources, list):
                raise ValueError(f'Invalid expected_sources at line {line_number}')
            for citation in sources:
                if not isinstance(citation, dict) or set(citation) != {'document_ref', 'version', 'content_sha256'}:
                    raise ValueError(f'Invalid source at line {line_number}')
                if not isinstance(citation['document_ref'], str) or not citation['document_ref'].strip():
                    raise ValueError(f'Invalid source reference at line {line_number}')
                if type(citation['version']) is not int or citation['version'] < 1:
                    raise ValueError(f'Invalid source version at line {line_number}')
                value = citation['content_sha256']
                if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError(f'Invalid source hash at line {line_number}')
            if item['answerable'] and not sources:
                raise ValueError(f'Answerable case lacks sources at line {line_number}')
    if len(seen) < minimum:
        raise ValueError(f'Blind set has {len(seen)} cases; requires at least {minimum}')
    return {'sha256': digest.hexdigest(), 'cases': len(seen),
            'answerable': answerable, 'unanswerable': len(seen) - answerable,
            'categories': dict(sorted(categories.items()))}
