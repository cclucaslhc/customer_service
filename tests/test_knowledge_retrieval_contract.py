"""The lexical seam preserves the repository's scoped candidate contract."""

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.knowledge import KnowledgeService, TOKENIZER_VERSION
from app.knowledge_models import CHUNKER_VERSION
from app.knowledge_retrieval import (KnowledgeIndexChanged, LexicalCandidates, RagConfigurationError, RagModelUnavailable,
                                     RepositoryLexicalRetriever, RepositoryVectorRetriever)
from app.rag_model_manifest import EMBEDDING_WEIGHT, RERANKER_WEIGHT
from app.tracing import SafeTracing
from app.identity import AuthenticationError,AuthorizationError
from app.local_inference_usage import LocalInferenceAccountingError
from types import SimpleNamespace


@pytest.mark.parametrize('error',[AuthenticationError(),AuthorizationError(),
    KnowledgeIndexChanged('synthetic'),LocalInferenceAccountingError('synthetic'),
    sa.exc.OperationalError('synthetic',{},Exception())])
def test_embedding_authority_and_accounting_errors_are_not_model_degradation(error):
    profile=dict(model_id=EMBEDDING_WEIGHT.repository,model_revision=EMBEDDING_WEIGHT.revision,
        weights_sha256=EMBEDDING_WEIGHT.sha256,license_id=EMBEDDING_WEIGHT.license_id,
        dimension=1024,distance='cosine',normalized=True,max_tokens=1024,
        lexical_version='test',chunker_version='test',reranker_version=None)
    def fail(*args,**kwargs): raise error
    repository=SimpleNamespace(active_vector_profile=lambda:profile)
    gateway=SimpleNamespace(max_length=1024,encode=fail)
    retriever=RepositoryVectorRetriever(repository,gateway,lexical_version='test',chunker_version='test')
    with pytest.raises(type(error)) as caught: retriever.candidates('synthetic',limit=1)
    assert caught.value is error


@pytest.mark.parametrize('error',[AuthenticationError(),AuthorizationError(),
    KnowledgeIndexChanged('synthetic'),LocalInferenceAccountingError('synthetic')])
def test_reranker_authority_and_accounting_errors_are_not_silently_ignored(error):
    class Repository:
        def setting(self,key): return None
        def search_candidates(self,terms,limit):
            return [dict(id=1,content='退货需要订单号。',section='退货',location='第1页',
                block_type='paragraph',metadata_json={},document_id='synthetic-doc',
                display_name='Synthetic',score=-.2,score_kind='postgres_ts_rank')],[]
        def assets(self,document_ids): return []
    def fail(*args,**kwargs): raise error
    service=KnowledgeService(repository=Repository(),reranker=SimpleNamespace(score=fail))
    with pytest.raises(type(error)) as caught: service.search('退货需要什么？')
    assert caught.value is error


def test_repository_lexical_retriever_preserves_ranked_and_character_candidates():
    class Repository:
        def search_candidates(self, terms, limit):
            assert terms == ["yc32m103"]
            assert limit == 20
            return ([{"id": 1, "score_kind": "postgres_ts_rank"}], [{"id": 2}])

    batch = RepositoryLexicalRetriever(Repository()).candidates(["yc32m103"], 20)
    assert batch == LexicalCandidates(
        ranked=[{"id": 1, "score_kind": "postgres_ts_rank"}],
        character_pool=[{"id": 2}],
    )


def test_vector_retriever_checks_pinned_active_profile_before_inference():
    class Gateway:
        max_length = 256
        def encode(self, texts, *, query=False):
            assert texts == ['退货要准备什么？'] and query
            return [[1.0] + [0.0] * 1023]

    class Repository:
        def active_vector_profile(self):
            return dict(profile_id='profile', model_id=EMBEDDING_WEIGHT.repository,
                model_revision=EMBEDDING_WEIGHT.revision, weights_sha256=EMBEDDING_WEIGHT.sha256,
                license_id=EMBEDDING_WEIGHT.license_id, dimension=1024, distance='cosine',
                normalized=True, max_tokens=256, lexical_version=TOKENIZER_VERSION,
                chunker_version=CHUNKER_VERSION, reranker_version=None)
        def search_vector_candidates(self, vector, *, profile_id, limit):
            assert len(vector) == 1024 and profile_id == 'profile' and limit == 20
            return [{'id': 1}]

    assert RepositoryVectorRetriever(Repository(), Gateway(), lexical_version=TOKENIZER_VERSION,
        chunker_version=CHUNKER_VERSION).candidates(
        '退货要准备什么？', limit=20) == [{'id': 1}]
    class StaleRepository(Repository):
        def active_vector_profile(self):
            return {**super().active_vector_profile(), 'chunker_version': 'obsolete'}
    with pytest.raises(RagConfigurationError, match='pipeline versions'):
        RepositoryVectorRetriever(StaleRepository(), Gateway(), lexical_version=TOKENIZER_VERSION,
            chunker_version=CHUNKER_VERSION).candidates('退货要准备什么？', limit=20)


def test_run_pinned_index_rejects_switch_before_and_during_vector_inference():
    class Repository:
        index_id = 'old'
        def active_vector_profile(self):
            return dict(index_id=self.index_id, profile_id='profile',
                model_id=EMBEDDING_WEIGHT.repository, model_revision=EMBEDDING_WEIGHT.revision,
                weights_sha256=EMBEDDING_WEIGHT.sha256, license_id=EMBEDDING_WEIGHT.license_id,
                dimension=1024, distance='cosine', normalized=True, max_tokens=256,
                lexical_version=TOKENIZER_VERSION, chunker_version=CHUNKER_VERSION,
                reranker_version=None)
        def search_vector_candidates(self, vector, *, profile_id, limit):
            return [{'id': 1}]
    repo = Repository()
    class Gateway:
        max_length = 256
        def encode(self, texts, *, query=False):
            repo.index_id = 'new'
            return [[1.0] + [0.0] * 1023]
    retriever = RepositoryVectorRetriever(repo, Gateway(), lexical_version=TOKENIZER_VERSION,
        chunker_version=CHUNKER_VERSION)
    with pytest.raises(KnowledgeIndexChanged):
        retriever.candidates('退货', limit=20, index_id='old')
    with pytest.raises(KnowledgeIndexChanged):
        retriever.candidates('退货', limit=20, index_id='old')


def test_hybrid_candidates_keep_scoped_vector_only_hit_and_fail_closed_on_wrong_tenant():
    class Repository:
        def setting(self, key):
            return None
        def storage_namespace(self):
            return 'tenant-a'
        def search_candidates(self, terms, limit):
            return [], []
        def assets(self, document_ids):
            return []

    class Vector:
        tenant = 'tenant-a'
        def candidates(self, query, *, limit):
            return [dict(id=10, tenant_id=self.tenant, document_id='document-a',
                document_version=1, content_sha256='a' * 64, content='退货授权需要订单号和批次。',
                section='退货', location='第1页', block_type='paragraph', metadata_json={},
                display_name='退货指南', distance=0.1)]

    vector = Vector()
    service = KnowledgeService(repository=Repository(), vector_retriever=vector)
    result = service.search('退货需要准备什么资料？')
    assert len(result) == 1 and result[0]['id'] == 10
    assert result[0]['ranking']['fusion_boost'] > 0
    vector.tenant = 'tenant-b'
    with pytest.raises(ValueError, match='cross-tenant'):
        service.search('退货需要准备什么资料？')


def test_hybrid_local_model_failure_only_degrades_to_scoped_lexical():
    class Repository:
        def setting(self, key):
            return None
        def search_candidates(self, terms, limit):
            row = dict(id=1, content='退货申请需要订单号。', section='退货', location='第1页',
                block_type='paragraph', metadata_json={}, document_id='document-a',
                display_name='退货指南', score=-0.2, score_kind='postgres_ts_rank')
            return [row], []
        def assets(self, document_ids):
            return []
    class Vector:
        def candidates(self, query, *, limit):
            raise RagModelUnavailable('synthetic offline failure')
    service = KnowledgeService(repository=Repository(), vector_retriever=Vector())
    assert service.search('退货需要什么？')[0]['id'] == 1
    assert service.last_degraded_reason == 'RagModelUnavailable'


def test_hybrid_database_failure_is_not_silently_downgraded():
    class Repository:
        def setting(self, key):
            return None
        def search_candidates(self, terms, limit):
            return [], []
    class Vector:
        def candidates(self, query, *, limit):
            raise sa.exc.OperationalError('synthetic scoped vector query', {}, Exception('offline'))
    service = KnowledgeService(repository=Repository(), vector_retriever=Vector())
    with pytest.raises(sa.exc.OperationalError):
        service.search('退货要准备什么？')


def test_search_rechecks_result_visibility_before_returning_text():
    class Repository:
        def setting(self, key):
            return None
        def search_candidates(self, terms, limit):
            return [dict(id=1, content='退货需要订单号。', section='退货', location='第1页',
                block_type='paragraph', metadata_json={}, document_id='document-a',
                display_name='退货指南', score=-0.2, score_kind='postgres_ts_rank')], []
        def assets(self, document_ids):
            return []
        def filter_current_results(self, rows):
            assert len(rows) == 1
            return []  # Synthetic concurrent revocation.
    assert KnowledgeService(repository=Repository()).search('退货需要什么？') == []


def test_search_refills_top_k_from_current_authorized_reserve_after_revocation():
    class Repository:
        def setting(self, key):
            return None
        def search_candidates(self, terms, limit):
            return [dict(id=number, content='退货需要订单号。', section='退货', location='第1页',
                block_type='paragraph', metadata_json={}, document_id=f'document-{number}',
                display_name='退货指南', score=-0.3 / number, score_kind='postgres_ts_rank')
                for number in (1, 2, 3)], []
        def assets(self, document_ids):
            return []
        def filter_current_results(self, rows):
            assert [row['id'] for row in rows] == [1, 2, 3]
            return rows[1:]  # First hit was replaced/revoked after retrieval.
    result = KnowledgeService(repository=Repository()).search('退货需要什么？', limit=1)
    assert [row['id'] for row in result] == [2]


def test_hybrid_stage_traces_have_fixed_labels_and_no_query_or_document_text():
    class Repository:
        def setting(self, key):
            return None
        def storage_namespace(self):
            return 'tenant-a'
        def search_candidates(self, terms, limit):
            return [], []
        def active_vector_profile(self):
            return dict(profile_id='profile', model_id=EMBEDDING_WEIGHT.repository,
                model_revision=EMBEDDING_WEIGHT.revision, weights_sha256=EMBEDDING_WEIGHT.sha256,
                license_id=EMBEDDING_WEIGHT.license_id, dimension=1024, distance='cosine',
                normalized=True, max_tokens=256, lexical_version=TOKENIZER_VERSION,
                chunker_version=CHUNKER_VERSION, reranker_version=RERANKER_WEIGHT.revision)
        def search_vector_candidates(self, vector, *, profile_id, limit):
            return [dict(id=10, tenant_id='tenant-a', document_id='document-a',
                document_version=1, content_sha256='a' * 64, content='SECRET_DOCUMENT_CONTENT',
                section='Synthetic', location='page', block_type='paragraph', metadata_json={},
                display_name='Synthetic', distance=0.1)]
        def assets(self, document_ids):
            return []
    class Gateway:
        max_length = 256
        def encode(self, texts, *, query=False):
            assert texts == ['SECRET_QUERY'] and query
            return [[1.0] + [0.0] * 1023]
    class Reranker:
        def score(self, query, documents):
            assert query == 'SECRET_QUERY' and documents == ['SECRET_DOCUMENT_CONTENT']
            return [0.9]
    exporter = InMemorySpanExporter()
    tracing = SafeTracing(exporter)
    try:
        with tracing.scope('rag.search', root=True, rag_stage='retrieve'):
            results = KnowledgeService(repository=Repository(), embedding_gateway=Gateway(),
                reranker=Reranker()).search('SECRET_QUERY')
        assert len(results) == 1
        assert tracing.flush()
        spans = exporter.get_finished_spans()
        assert {span.name for span in spans} == {'rag.search', 'rag.embed_query',
            'rag.vector', 'rag.fuse', 'rag.rerank'}
        evidence = str([(span.name, dict(span.attributes)) for span in spans])
        assert 'SECRET_QUERY' not in evidence and 'SECRET_DOCUMENT_CONTENT' not in evidence
        assert tracing.snapshot()['rejected'] == 0
    finally:
        tracing.close()
