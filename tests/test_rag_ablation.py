from scripts.evaluate_rag_local import metrics, ranked


def test_development_ablation_identity_and_rank_metrics():
    candidates = ranked(['a.txt', 'b.txt'], 'vector', {'a.txt': 'first', 'b.txt': 'second'})
    assert [item.rank for item in candidates] == [1, 2]
    assert candidates[0].content_sha256 != candidates[1].content_sha256
    assert metrics([{'rank': 1}, {'rank': 4}, {'rank': None}]) == {
        'count': 3, 'recall_at_1': 1 / 3, 'recall_at_3': 1 / 3,
    }
