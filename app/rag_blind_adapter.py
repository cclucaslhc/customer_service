"""Explicit, revocable identity binding for private retrieval blind tests."""

import hashlib
import uuid
from dataclasses import dataclass, field

from app.identity import AuthorizationError
from app.knowledge import KnowledgeService
from app.persistence.identity_repository import IdentityRepository
from app.persistence.knowledge_repository import KnowledgeRepository


@dataclass(frozen=True)
class BlindBinding:
    tenant_id: str
    role: str
    token: str = field(repr=False)
    document_ref_by_id: dict[str, str] = field(repr=False)


class AuthenticatedBlindSearch:
    """Run each question through the same tenant-scoped KnowledgeService path.

    Bindings and the private document-reference mapping are injected in memory
    by an approved runner; no credentials, text, or source mapping are read from
    environment variables or emitted in reports.
    """

    def __init__(self, engine, bindings: dict[tuple[str, str], BlindBinding], *,
                 embedding_gateway=None, reranker=None):
        if engine.dialect.name != 'postgresql' or not bindings:
            raise ValueError('Blind retrieval requires PostgreSQL and explicit bindings')
        self.engine = engine
        self.bindings = dict(bindings)
        self.embedding_gateway = embedding_gateway
        self.reranker = reranker
        self._index_ids = {}
        for (alias, role), binding in self.bindings.items():
            if (not isinstance(alias, str) or not alias or role != binding.role or
                    str(uuid.UUID(binding.tenant_id)) != binding.tenant_id or
                    not binding.document_ref_by_id):
                raise ValueError('Invalid blind retrieval binding')
            if any(str(uuid.UUID(key)) != key or not isinstance(value, str) or not value
                   for key, value in binding.document_ref_by_id.items()):
                raise ValueError('Invalid blind source mapping')
            if len(set(binding.document_ref_by_id.values())) != len(binding.document_ref_by_id):
                raise ValueError('Duplicate blind source reference')

    def __call__(self, tenant_alias: str, role: str, question: str, *, limit: int):
        if type(limit) is not int or limit != 3:
            raise ValueError('Blind retrieval is fixed to Top-3')
        binding = self.bindings.get((tenant_alias, role))
        if binding is None:
            raise AuthorizationError('Blind identity binding unavailable')
        principal = IdentityRepository(self.engine).resolve(binding.token)
        if principal.tenant_id != binding.tenant_id or principal.role != role:
            raise AuthorizationError('Blind identity binding changed')
        principal.require('knowledge.read')
        repository = KnowledgeRepository(self.engine).authenticated(binding.token)
        service = KnowledgeService(repository=repository,
            embedding_gateway=self.embedding_gateway, reranker=self.reranker)
        key = (tenant_alias, role)
        if key not in self._index_ids:
            self._index_ids[key] = service.active_index_id()
        rows = service.search(question, limit=limit,
            index_id=self._index_ids[key], pinned=True)
        results = []
        for row in rows:
            if str(row.get('tenant_id')) != binding.tenant_id:
                raise AuthorizationError('Blind retrieval crossed tenant boundary')
            document_id = str(row['document_id'])
            reference = binding.document_ref_by_id.get(document_id)
            if reference is None:
                raise ValueError('Blind source mapping is incomplete')
            results.append({'tenant_alias': tenant_alias, 'document_ref': reference,
                'version': row['document_version'],
                'content_sha256': row.get('content_sha256') or
                    hashlib.sha256(row['content'].encode('utf-8')).hexdigest()})
        return results
