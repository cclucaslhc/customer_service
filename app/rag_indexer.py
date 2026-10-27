"""Restartable, tenant-scoped Embedding builder for current PostgreSQL chunks.

Inference happens outside the database transaction. Publication rechecks the
document version and immutable chunk hash so a concurrent replacement cannot
make a stale vector visible.
"""

import hashlib
import math
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.persistence.schema import metadata
from app.rag_model_manifest import EMBEDDING_WEIGHT


_MISSING = sa.text("""
SELECT v.document_id,v.document_version,v.chunk_index,v.content_sha256,v.content
FROM knowledge_chunk_versions v JOIN documents d
  ON d.tenant_id=v.tenant_id AND d.id=v.document_id
WHERE v.tenant_id=:tenant AND d.status='ready' AND d.version=v.document_version
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_embeddings_1024 e
    WHERE e.tenant_id=v.tenant_id AND e.profile_id=:profile
      AND e.document_id=v.document_id AND e.document_version=v.document_version
      AND e.chunk_index=v.chunk_index)
ORDER BY v.document_id,v.document_version,v.chunk_index
LIMIT :limit
""")


class RagIndexWorker:
    def __init__(self, engine, *, tenant_id, profile_id, embedding_gateway):
        if engine.dialect.name != 'postgresql':
            raise ValueError('RAG index Worker requires PostgreSQL')
        self.engine = engine
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.profile_id = uuid.UUID(str(profile_id))
        self.embedding_gateway = embedding_gateway

    def _check_profile(self, db):
        profiles = metadata.tables['embedding_profiles']
        row = db.execute(sa.select(profiles).where(
            profiles.c.tenant_id == self.tenant_id,
            profiles.c.profile_id == self.profile_id)).mappings().first()
        if not row or (row['model_id'], row['model_revision'], row['weights_sha256'],
                       row['license_id'], row['dimension'], row['distance'], row['normalized']) != (
                           EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                           EMBEDDING_WEIGHT.sha256, EMBEDDING_WEIGHT.license_id,
                           1024, 'cosine', True):
            raise ValueError('RAG profile is not the pinned local Embedding model')
        if hasattr(self.embedding_gateway, 'max_length') and row['max_tokens'] != self.embedding_gateway.max_length:
            raise ValueError('Embedding input limit does not match the frozen profile')

    @staticmethod
    def _validated_vectors(vectors, count):
        if len(vectors) != count:
            raise ValueError('Embedding batch count mismatch')
        for vector in vectors:
            if len(vector) != 1024 or not all(isinstance(value, (int, float))
                    and not isinstance(value, bool) and math.isfinite(value) for value in vector):
                raise ValueError('Invalid Embedding vector')
            if not 0.99 <= math.sqrt(sum(float(value) ** 2 for value in vector)) <= 1.01:
                raise ValueError('Embedding vector must be normalized')

    def run_batch(self, *, batch_size=16):
        """Return inserted count; zero means no missing current chunks were found."""
        if not 1 <= batch_size <= 16:
            raise ValueError('Embedding batch must contain 1-16 chunks')
        with self.engine.connect() as db:
            from app.recovery import require_operational
            require_operational(db)
            self._check_profile(db)
            rows = db.execute(_MISSING, {'tenant': self.tenant_id,
                'profile': self.profile_id, 'limit': batch_size}).mappings().all()
        if not rows:
            return 0
        if any(hashlib.sha256(row['content'].encode('utf-8')).hexdigest() != row['content_sha256']
               for row in rows):
            raise ValueError('RAG snapshot content hash mismatch')
        vectors = self.embedding_gateway.encode([row['content'] for row in rows])
        self._validated_vectors(vectors, len(rows))
        table = metadata.tables['knowledge_embeddings_1024']
        documents = metadata.tables['documents']
        snapshots = metadata.tables['knowledge_chunk_versions']
        inserted = 0
        with self.engine.begin() as db:
            from app.recovery import require_operational
            require_operational(db)
            self._check_profile(db)
            for row, vector in zip(rows, vectors):
                # Row locks serialize against publication/deletion while the
                # vector is committed. An old version is safe to skip.
                current = db.execute(sa.select(documents.c.version).where(
                    documents.c.tenant_id == self.tenant_id,
                    documents.c.id == row['document_id'],
                    documents.c.status == 'ready').with_for_update()).scalar_one_or_none()
                if current != row['document_version']:
                    continue
                source = db.execute(sa.select(snapshots.c.content_sha256, snapshots.c.content).where(
                    snapshots.c.tenant_id == self.tenant_id,
                    snapshots.c.document_id == row['document_id'],
                    snapshots.c.document_version == row['document_version'],
                    snapshots.c.chunk_index == row['chunk_index']).with_for_update()).first()
                if source != (row['content_sha256'], row['content']):
                    continue
                result = db.execute(pg_insert(table).values(
                    tenant_id=self.tenant_id, profile_id=self.profile_id,
                    document_id=row['document_id'], document_version=row['document_version'],
                    chunk_index=row['chunk_index'], content_sha256=row['content_sha256'],
                    embedding=[float(value) for value in vector], embedded_at=datetime.now(timezone.utc)
                ).on_conflict_do_nothing().returning(table.c.chunk_index)).first()
                inserted += bool(result)
        return inserted
