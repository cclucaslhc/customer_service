"""Opt-in verified weights + real tokenizer + durable PG inference receipts."""
import gc
import os
from pathlib import Path
import sqlalchemy as sa
import pytest
from app.local_inference_usage import AuditInferenceMeter
from app.persistence.schema import metadata
from app.rag_local import (LocalEmbeddingGateway,LocalReranker,EMBEDDING_INSTRUCTION,
    RERANK_INSTRUCTION)
from app.rag_model_manifest import EMBEDDING_WEIGHT,RERANKER_WEIGHT
from test_local_inference_usage import ledger,postgres_database


@pytest.mark.requires_local_rag_model
def test_real_local_weights_tokenizer_and_receipts_agree(ledger):
    configured=os.environ.get('TEST_RAG_MODEL_ROOT')
    if not configured: pytest.skip('TEST_RAG_MODEL_ROOT unset; real local metering not verified')
    root=Path(configured)
    assert root.is_absolute() and root.is_dir() and not root.is_symlink()
    import torch
    device='cuda' if torch.cuda.is_available() else 'cpu'
    bundle,token,_=ledger
    repository=bundle.knowledge.authenticated(token)
    query='退货需要什么资料？'
    documents=['退货授权需要订单号和产品批次。','产品停产通知应写明最后订购日期。']
    embedding=LocalEmbeddingGateway(root/'embedding',device=device,max_length=256).with_meter(
        AuditInferenceMeter.for_knowledge(repository,EMBEDDING_WEIGHT))
    expected_query=len(embedding.tokenizer.encode(f'Instruct: {EMBEDDING_INSTRUCTION}\nQuery:{query}',
        truncation=True,max_length=256))
    expected_documents=sum(len(embedding.tokenizer.encode(text,truncation=True,max_length=256)) for text in documents)
    vector=embedding.encode([query],query=True)[0]
    vectors=embedding.encode(documents)
    assert len(vector)==len(vectors[0])==1024
    assert sum(a*b for a,b in zip(vector,vectors[0]))>sum(a*b for a,b in zip(vector,vectors[1]))
    del embedding
    gc.collect()
    if device=='cuda': torch.cuda.empty_cache()
    reranker=LocalReranker(root/'reranker',device=device,max_length=512).with_meter(
        AuditInferenceMeter.for_knowledge(repository,RERANKER_WEIGHT))
    budget=512-len(reranker.prefix_tokens)-len(reranker.suffix_tokens)
    expected_rerank=sum(len(reranker.prefix_tokens)+len(reranker.suffix_tokens)+len(
        reranker.tokenizer.encode(f'<Instruct>: {RERANK_INSTRUCTION}\n<Query>:{query}\n<Document>:{document}',
            add_special_tokens=False,truncation=True,max_length=budget)) for document in documents)
    scores=reranker.score(query,documents)
    assert len(scores)==2 and 0<=scores[1]<scores[0]<=1
    del reranker
    gc.collect()
    if device=='cuda': torch.cuda.empty_cache()
    with bundle.engine.connect() as db:
        receipts=db.execute(sa.select(metadata.tables['audit_logs'].c.metadata_json).where(
            metadata.tables['audit_logs'].c.action=='local_inference.finished')).scalars().all()
    assert len(receipts)==3
    assert {row['purpose']:row['input_tokens'] for row in receipts}=={
        'embedding_query':expected_query,'embedding_document':expected_documents,'rerank':expected_rerank}
    assert all(row['usage_status']=='tokenizer_observed' and row['state']=='completed' for row in receipts)
    assert all(row['infrastructure_amount'] is None for row in receipts)
