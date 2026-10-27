"""Opt-in real local-model inference; never downloads or calls a remote API."""

import os
from pathlib import Path

import pytest

from app.rag_local import LocalEmbeddingGateway, LocalReranker


def test_pinned_local_embedding_and_reranker():
    root_value = os.environ.get('TEST_RAG_MODEL_ROOT')
    if not root_value:
        pytest.skip('TEST_RAG_MODEL_ROOT unset; local-model inference not verified in this run')
    root = Path(root_value)
    import torch
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    embedder = LocalEmbeddingGateway(root / 'embedding', device=device, max_length=256)
    query = embedder.encode(['退货需要什么资料？'], query=True)[0]
    docs = embedder.encode(['退货授权需要订单号和产品批次。', '产品停产通知应写明最后订购日期。'])
    assert len(query) == len(docs[0]) == 1024
    assert sum(a * b for a, b in zip(query, docs[0])) > sum(a * b for a, b in zip(query, docs[1]))
    del embedder
    if device == 'cuda':
        torch.cuda.empty_cache()
    reranker = LocalReranker(root / 'reranker', device=device, max_length=512)
    scores = reranker.score('退货需要什么资料？',
                            ['退货授权需要订单号和产品批次。', '产品停产通知应写明最后订购日期。'])
    assert len(scores) == 2 and 0 <= scores[1] < scores[0] <= 1
