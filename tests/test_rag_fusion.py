import pytest

from app.rag_fusion import RankedCandidate, reciprocal_rank_fusion


def candidate(document, channel, rank, *, tenant='tenant-a', version=1, digest='a'):
    return RankedCandidate(tenant, document, version, f'{document}-{version}',
                           digest * 64, channel, rank)


def test_fusion_rewards_agreement_without_mixing_raw_scores():
    result = reciprocal_rank_fusion({
        'lexical': [candidate('A', 'lexical', 1), candidate('B', 'lexical', 2)],
        'vector': [candidate('B', 'vector', 1), candidate('C', 'vector', 2)],
    }, tenant_id='tenant-a')
    assert [item.candidate.document_id for item in result] == ['B', 'A', 'C']
    assert result[0].channels == ('lexical', 'vector')


def test_fusion_rejects_cross_tenant_and_keeps_document_versions_distinct():
    with pytest.raises(ValueError, match='cross-tenant'):
        reciprocal_rank_fusion({'vector': [candidate('A', 'vector', 1, tenant='tenant-b')]},
                               tenant_id='tenant-a')
    result = reciprocal_rank_fusion({
        'lexical': [candidate('A', 'lexical', 1, version=1)],
        'vector': [candidate('A', 'vector', 1, version=2)],
    }, tenant_id='tenant-a')
    assert len(result) == 2


def test_duplicate_same_document_hash_counts_only_once_per_channel():
    first = candidate('A', 'lexical', 1)
    duplicate = RankedCandidate(first.tenant_id, first.document_id, first.document_version,
                                'another-chunk', first.content_sha256, 'lexical', 2)
    result = reciprocal_rank_fusion({'lexical': [first, duplicate]}, tenant_id='tenant-a')
    assert len(result) == 1
    assert result[0].score == 1 / 61
