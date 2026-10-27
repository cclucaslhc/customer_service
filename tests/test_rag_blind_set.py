import json

import pytest

from app.rag_blind_set import validate_blind_set


def case(number):
    return {'case_id': f'SYN-{number}', 'tenant_alias': 'synthetic', 'role': 'customer',
            'question': '合成问题', 'answerable': True, 'category': ['synthetic'],
            'expected_facts': ['合成事实'], 'forbidden_claims': [],
            'expected_sources': [{'document_ref': 'synthetic-guide', 'version': 1,
                                  'content_sha256': 'a' * 64}]}


def test_validates_frozen_shape_and_counts_without_returning_content(tmp_path):
    path = tmp_path / 'blind.jsonl'
    path.write_text(''.join(json.dumps(case(i), ensure_ascii=False) + '\n'
                            for i in range(200)), encoding='utf-8')
    report = validate_blind_set(path)
    assert report['cases'] == report['answerable'] == 200
    assert report['categories'] == {'synthetic': 200}
    assert '合成问题' not in repr(report)


def test_rejects_duplicate_and_missing_source(tmp_path):
    path = tmp_path / 'blind.jsonl'
    path.write_text(json.dumps(case(1)) + '\n' + json.dumps(case(1)) + '\n')
    with pytest.raises(ValueError, match='duplicate'):
        validate_blind_set(path, minimum=1)
    bad = case(2)
    bad['expected_sources'] = []
    path.write_text(json.dumps(bad) + '\n')
    with pytest.raises(ValueError, match='lacks sources'):
        validate_blind_set(path, minimum=1)


def test_rejects_too_few_cases_and_invalid_hash(tmp_path):
    path = tmp_path / 'blind.jsonl'
    path.write_text(json.dumps(case(1)) + '\n')
    with pytest.raises(ValueError, match='requires at least 200'):
        validate_blind_set(path)
    bad = case(1)
    bad['expected_sources'][0]['content_sha256'] = 'not-a-hash'
    path.write_text(json.dumps(bad) + '\n')
    with pytest.raises(ValueError, match='source hash'):
        validate_blind_set(path, minimum=1)
