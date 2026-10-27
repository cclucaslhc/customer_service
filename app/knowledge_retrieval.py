"""Candidate-source contracts for the staged RAG migration.

Candidate sources must enforce tenant and document visibility *inside* their
queries. A post-fusion filter cannot repair a cross-tenant candidate leak.
"""

from dataclasses import dataclass
from typing import Protocol

from app.rag_model_manifest import EMBEDDING_WEIGHT
from app.tracing import operation_scope


@dataclass(frozen=True)
class LexicalCandidates:
    ranked: list[dict]
    character_pool: list[dict]


class LexicalRetriever(Protocol):
    def candidates(self, terms: list[str], limit: int) -> LexicalCandidates: ...


class VectorRetriever(Protocol):
    def candidates(self, query: str, *, limit: int) -> list[dict]: ...


class RepositoryLexicalRetriever:
    """Current FTS and bounded character fallback, scoped by the repository."""

    def __init__(self, repository):
        self.repository = repository

    def candidates(self, terms: list[str], limit: int) -> LexicalCandidates:
        ranked, character_pool = self.repository.search_candidates(terms, limit)
        return LexicalCandidates(ranked, character_pool)


class RagModelUnavailable(RuntimeError):
    """Local inference failed; scoped lexical search may continue."""


class RagConfigurationError(ValueError):
    """Active profile cannot be served by the configured local model."""


class KnowledgeEvidenceChanged(RuntimeError):
    """A cited chunk was replaced or revoked before answer commit."""


class KnowledgeIndexChanged(RuntimeError):
    """The active RAG index changed during this Agent Run."""


class RepositoryVectorRetriever:
    def __init__(self, repository, embedding_gateway, *, lexical_version, chunker_version,
                 reranker_version=None):
        self.repository = repository
        self.embedding_gateway = embedding_gateway
        self.lexical_version = lexical_version
        self.chunker_version = chunker_version
        self.reranker_version = reranker_version

    def candidates(self, query: str, *, limit: int, index_id=None) -> list[dict]:
        profile = self.repository.active_vector_profile()
        if index_id is not None and (profile or {}).get('index_id') != index_id:
            raise KnowledgeIndexChanged()
        if profile is None:
            return []
        if (profile['model_id'], profile['model_revision'], profile['weights_sha256'],
                profile['license_id'], profile['dimension'], profile['distance'],
                profile['normalized']) != (
                    EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                    EMBEDDING_WEIGHT.sha256, EMBEDDING_WEIGHT.license_id,
                    1024, 'cosine', True):
            raise RagConfigurationError('Active profile is not the pinned local model')
        if profile['max_tokens'] != self.embedding_gateway.max_length:
            raise RagConfigurationError('Active profile input limit does not match local model')
        if (profile['lexical_version'], profile['chunker_version'],
                profile['reranker_version']) != (self.lexical_version,
                    self.chunker_version, self.reranker_version):
            raise RagConfigurationError('Active index pipeline versions do not match runtime')
        try:
            with operation_scope('rag.embed_query', rag_stage='embed_query'):
                vector = self.embedding_gateway.encode([query], query=True)[0]
        except Exception as exc:
            raise RagModelUnavailable('Local query Embedding unavailable') from exc
        # The repository rechecks active status and current document version in
        # the same scoped SQL query; a concurrent index switch yields no rows.
        with operation_scope('rag.vector', rag_stage='vector'):
            rows = self.repository.search_vector_candidates(vector,
                profile_id=profile['profile_id'], limit=limit)
        if index_id is not None and (self.repository.active_vector_profile() or {}).get('index_id') != index_id:
            raise KnowledgeIndexChanged()
        return rows
