import json

import pytest

from app.rag_blind_eval import evaluate_retrieval


def _case(number, *, answerable=True):
    return {'case_id': f'SYN-{number}', 'tenant_alias': 'synthetic-tenant',
            'role': 'customer', 'question': 'SYNTHETIC_PRIVATE_QUESTION',
            'answerable': answerable, 'category': ['synthetic'],
            'expected_facts': ['synthetic fact'] if answerable else [],
            'forbidden_claims': [], 'expected_sources': [
                {'document_ref': 'synthetic-document', 'version': 1,
                 'content_sha256': 'a' * 64}] if answerable else []}


def test_blind_retrieval_scores_without_returning_sensitive_content(tmp_path):
    path = tmp_path / 'synthetic-private.jsonl'
    path.write_text(''.join(json.dumps(_case(index, answerable=index < 150)) + '\n'
                            for index in range(200)), encoding='utf-8')
    calls = []
    def search(tenant_alias, role, question, *, limit):
        calls.append((tenant_alias, role, limit))
        assert question == 'SYNTHETIC_PRIVATE_QUESTION'
        if len(calls) > 150:
            return []
        return [{'tenant_alias': tenant_alias, 'document_ref': 'synthetic-document',
                 'version': 1, 'content_sha256': 'a' * 64}]
    report = evaluate_retrieval(path, search)
    assert report['cases'] == 200 and report['answerable_case_hit_at_3'] == 1
    assert report['expected_source_recall_at_3'] == 1
    assert report['unanswerable_empty_results'] == 50
    assert report['cross_tenant_results'] == 0
    assert calls == [('synthetic-tenant', 'customer', 3)] * 200
    assert 'SYNTHETIC_PRIVATE_QUESTION' not in repr(report)
    assert 'synthetic-document' not in repr(report)


def test_blind_retrieval_counts_cross_tenant_and_rejects_unbounded_adapter(tmp_path):
    path = tmp_path / 'synthetic-private.jsonl'
    path.write_text(json.dumps(_case(1)) + '\n', encoding='utf-8')
    def cross_tenant(*args, **kwargs):
        return [{'tenant_alias': 'wrong-tenant', 'document_ref': 'synthetic-document',
                 'version': 1, 'content_sha256': 'a' * 64}]
    report = evaluate_retrieval(path, cross_tenant, minimum=1)
    assert report['cross_tenant_results'] == 1
    assert report['answerable_case_hit_at_3'] == 0
    def excessive(*args, **kwargs):
        return cross_tenant() * 4
    with pytest.raises(ValueError, match='Top-3'):
        evaluate_retrieval(path, excessive, minimum=1)
