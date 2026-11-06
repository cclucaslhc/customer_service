"""Local inference attempts: no weights, documents, online models or invoices."""
import json
from types import MethodType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.local_inference_usage import AuditInferenceMeter,LocalInferenceObservation
from app.rag_local import LocalEmbeddingGateway,LocalReranker
from app.rag_model_manifest import EMBEDDING_WEIGHT
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.schema import metadata
from test_postgresql_integration import postgres_database


class RecordingMeter(AuditInferenceMeter):
    def __init__(self,fail_terminal=False):
        self.events=[]
        self.fail_terminal=fail_terminal
    def begin(self,*args):
        self.events.append(('begin',args))
        return 'synthetic-attempt'
    def finish(self,*args):
        self.events.append(('finish',args))
        if self.fail_terminal: raise RuntimeError('Synthetic accounting failure')


@pytest.mark.parametrize('value',[None,True,0,-1,1.5,16*8192+1])
def test_token_observation_rejects_invalid_values(value):
    with pytest.raises(ValueError): LocalInferenceObservation().observe_tokens(value)


@pytest.mark.parametrize('query,purpose',[(True,'embedding_query'),(False,'embedding_document')])
def test_embedding_meter_is_per_clone_and_does_not_receive_content(query,purpose):
    original=object.__new__(LocalEmbeddingGateway)
    original.max_length=1024
    def encode(self,texts,is_query,observation):
        observation.observe_tokens(17)
        return [[1.0]]
    original._encode=MethodType(encode,original)
    meter=RecordingMeter()
    clone=original.with_meter(meter)
    # Bind the test stub to the clone, just as normal class methods are bound.
    clone._encode=MethodType(encode,clone)
    assert clone.encode(['synthetic-private-text'],query=query)==[[1.0]]
    assert meter.events==[('begin',(purpose,1,1024)),('finish',('synthetic-attempt','completed',17))]
    assert not hasattr(original,'usage_meter')
    assert 'private-text' not in str(meter.events)


def test_reranker_failure_preserves_observed_input_without_success():
    gateway=object.__new__(LocalReranker)
    gateway.max_length=1024
    def score(self,query,documents,observation):
        observation.observe_tokens(23)
        raise RuntimeError('Synthetic model failure')
    gateway._score=MethodType(score,gateway)
    meter=RecordingMeter()
    gateway=gateway.with_meter(meter)
    with pytest.raises(RuntimeError,match='model failure'):
        gateway.score('synthetic-query',['synthetic-document'])
    assert meter.events[-1]==('finish',('synthetic-attempt','error',23))


def test_terminal_accounting_failure_does_not_return_result():
    meter=RecordingMeter(fail_terminal=True)
    with pytest.raises(RuntimeError,match='accounting failure'):
        with meter.measure('embedding_query',1,1024) as observation:
            observation.observe_tokens(10)


def test_failed_tokenization_does_not_fabricate_zero_usage():
    meter=RecordingMeter()
    with pytest.raises(ValueError):
        with meter.measure('rerank',1,1024): raise ValueError('Synthetic tokenizer failure')
    assert meter.events[-1]==('finish',('synthetic-attempt','error',None))


@pytest.fixture
def ledger(postgres_database):
    command.upgrade(Config('alembic.ini'),'head')
    bundle=PostgreSQLRepositories.from_url(postgres_database,pool_size=3)
    tenant=bundle.identity.create_tenant('Synthetic inference accounting')
    bundle.identity.provision_user('synthetic.inference','synthetic-password-only',tenant,role='admin')
    token=bundle.identity.login('synthetic.inference','synthetic-password-only',tenant).token
    meter=AuditInferenceMeter.for_knowledge(bundle.knowledge.authenticated(token),EMBEDDING_WEIGHT)
    try: yield bundle,token,meter
    finally: bundle.close()


def test_real_audit_receipts_are_idempotent_bounded_and_content_free(ledger):
    bundle,token,meter=ledger
    attempt=meter.begin('embedding_query',1,1024)
    meter.finish(attempt,'completed',17)
    meter.finish(attempt,'completed',17)
    with pytest.raises(ValueError,match='conflict'): meter.finish(attempt,'error',17)
    with bundle.engine.connect() as db:
        rows=db.execute(sa.select(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.entity_id==attempt)).mappings().all()
    assert len(rows)==2
    terminal=next(row['metadata_json'] for row in rows if row['action']=='local_inference.finished')
    assert terminal['input_tokens']==17 and terminal['usage_status']=='tokenizer_observed'
    assert terminal['api_billing']=='not_applicable_offline_v1'
    assert terminal['infrastructure_amount'] is None
    assert all('content' not in row['metadata_json'] for row in rows)


def test_real_authority_revocation_leaves_started_receipt_unresolved(ledger):
    bundle,token,meter=ledger
    attempt=meter.begin('rerank',1,1024)
    bundle.identity.logout(token)
    from app.identity import AuthenticationError
    with pytest.raises(AuthenticationError): meter.finish(attempt,'completed',10)
    with bundle.engine.connect() as db:
        actions=db.execute(sa.select(metadata.tables['audit_logs'].c.action).where(
            metadata.tables['audit_logs'].c.entity_id==attempt)).scalars().all()
    assert actions==['local_inference.started']


def test_real_accounting_rejects_tokens_over_predeclared_bound(ledger):
    _,_,meter=ledger
    attempt=meter.begin('embedding_document',1,100)
    with pytest.raises(ValueError,match='declared bound'): meter.finish(attempt,'completed',101)


def test_real_knowledge_service_binds_both_local_gateways_without_shared_meter_mutation(ledger,tmp_path):
    from app.knowledge import KnowledgeService
    bundle,token,_=ledger
    embedding=object.__new__(LocalEmbeddingGateway)
    embedding.max_length=1024
    reranker=object.__new__(LocalReranker)
    reranker.max_length=1024
    service=KnowledgeService(repository=bundle.knowledge.authenticated(token),
        knowledge_dir=tmp_path/'docs',asset_dir=tmp_path/'assets',
        embedding_gateway=embedding,reranker=reranker)
    assert service.vector_retriever.embedding_gateway is not embedding
    assert service.reranker is not reranker
    assert not hasattr(embedding,'usage_meter') and not hasattr(reranker,'usage_meter')
    def encode(self,texts,query,observation):
        observation.observe_tokens(11)
        return [[1.0]]
    def score(self,query,documents,observation):
        observation.observe_tokens(19)
        return [.5]
    gateway=service.vector_retriever.embedding_gateway
    gateway._encode=MethodType(encode,gateway)
    service.reranker._score=MethodType(score,service.reranker)
    assert gateway.encode(['synthetic-private-content'],query=True)==[[1.0]]
    assert service.reranker.score('synthetic-private-query',['synthetic-private-content'])==[.5]
    with bundle.engine.connect() as db:
        rows=db.execute(sa.select(metadata.tables['audit_logs'].c.metadata_json).where(
            metadata.tables['audit_logs'].c.action=='local_inference.finished')).scalars().all()
    assert {row['purpose']:row['input_tokens'] for row in rows}=={'embedding_query':11,'rerank':19}
    assert 'synthetic-private' not in json.dumps(rows)


def test_real_concurrent_terminal_receipts_commit_only_once(ledger):
    from concurrent.futures import ThreadPoolExecutor
    bundle,_,meter=ledger
    attempt=meter.begin('embedding_document',1,1024)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:meter.finish(attempt,'completed',21),range(2)))
    with bundle.engine.connect() as db:
        count=db.execute(sa.select(sa.func.count()).select_from(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.entity_id==attempt,
            metadata.tables['audit_logs'].c.action=='local_inference.finished')).scalar_one()
    assert count==1


def test_real_index_worker_records_control_actor_without_inventing_tenant_user(ledger,tmp_path):
    import uuid
    from app.knowledge import KnowledgeService
    from app.rag_index_admin import RagIndexAdmin
    from app.rag_indexer import RagIndexWorker
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    bundle,token,_=ledger
    tenant=bundle.identity.resolve(token).tenant_id
    service=KnowledgeService(repository=bundle.knowledge.authenticated(token),
        knowledge_dir=tmp_path/'docs',asset_dir=tmp_path/'assets')
    service.ingest('synthetic.txt',b'Synthetic index accounting evidence.')
    profile,index=uuid.uuid4(),uuid.uuid4()
    RagIndexAdmin(bundle.engine).stage(tenant_id=tenant,profile_id=profile,index_id=index,
        max_tokens=1024,lexical_version='synthetic',chunker_version='synthetic')
    gateway=object.__new__(LocalEmbeddingGateway)
    gateway.max_length=1024
    def encode(self,texts,query,observation):
        observation.observe_tokens(13)
        return [[1.0]+[0.0]*1023 for _ in texts]
    gateway._encode=MethodType(encode,gateway)
    worker=RagIndexWorker(bundle.engine,tenant_id=tenant,profile_id=profile,embedding_gateway=gateway)
    assert worker.run_batch()==1
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.action=='local_inference.finished')).mappings().one()
    assert str(row['tenant_id'])==LEGACY_TENANT_ID and row['actor_id'] is None
    assert row['metadata_json']['target_tenant_id']==tenant
    assert row['metadata_json']['purpose']=='embedding_document'
    assert row['metadata_json']['input_tokens']==13
