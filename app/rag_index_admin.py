"""Owner-only staging and atomic activation for tenant RAG indexes."""

import hashlib
import math
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from app.persistence.schema import metadata
from app.rag_model_manifest import EMBEDDING_WEIGHT
from app.recovery import require_operational
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID


RAG_PUBLICATION_LOCK = 1129533503
_UNSET = object()


class RagIndexAdmin:
    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('RAG index administration requires PostgreSQL')
        self.engine = engine

    @staticmethod
    def _guard(db, *, shared=False):
        require_operational(db)
        owner = db.execute(sa.text("""SELECT pg_get_userbyid(c.relowner)=current_user
            FROM pg_class c WHERE c.oid='public.knowledge_index_versions'::regclass""")).scalar_one()
        if not owner:
            raise PermissionError('RAG index administration requires the migration owner role')
        lock = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
        db.execute(sa.text(f'SELECT {lock}(:key)'), {'key': RAG_PUBLICATION_LOCK})

    @staticmethod
    def _audit(db, tenant, index, action, details):
        # Database owners are not tenant members. Keep operator audit in the
        # existing control scope; never invent a tenant user attribution.
        db.execute(metadata.tables['audit_logs'].insert().values(id=uuid.uuid4(),tenant_id=uuid.UUID(LEGACY_TENANT_ID),
            actor_id=None,actor=db.execute(sa.text('SELECT current_user')).scalar_one(),
            action='rag.index.'+action,
            entity_type='knowledge_tenant' if action=='prune_stale' else 'knowledge_index',entity_id=str(index),
            summary='Controlled database-owner RAG index operation',
            metadata_json={'target_tenant_id':str(tenant),**details},
            created_at=sa.func.clock_timestamp()))

    def stage(self, *, tenant_id, profile_id, index_id, max_tokens,
              lexical_version, chunker_version, reranker_version=None):
        if not 64 <= max_tokens <= 8192 or not lexical_version or not chunker_version:
            raise ValueError('Invalid pinned RAG profile or index version')
        tenant, profile, index = (uuid.UUID(str(value)) for value in
                                  (tenant_id, profile_id, index_id))
        profiles = metadata.tables['embedding_profiles']
        indexes = metadata.tables['knowledge_index_versions']
        now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            self._guard(db)
            db.execute(profiles.insert().values(tenant_id=tenant, profile_id=profile,
                model_id=EMBEDDING_WEIGHT.repository, model_revision=EMBEDDING_WEIGHT.revision,
                weights_sha256=EMBEDDING_WEIGHT.sha256, license_id=EMBEDDING_WEIGHT.license_id,
                dimension=1024, distance='cosine', normalized=True, max_tokens=max_tokens,
                created_at=now))
            db.execute(indexes.insert().values(tenant_id=tenant, index_id=index,
                profile_id=profile, status='building', lexical_version=lexical_version,
                chunker_version=chunker_version, reranker_version=reranker_version,
                created_at=now))
            self._audit(db,tenant,index,'stage',{'profile_id':str(profile)})

    def stage_existing(self, *, tenant_id, profile_id, index_id,
                       lexical_version, chunker_version, reranker_version=None):
        """Create a new index identity for a previously verified profile, e.g. rollback."""
        if not lexical_version or not chunker_version:
            raise ValueError('Invalid RAG index version')
        tenant, profile, index = (uuid.UUID(str(value)) for value in
                                  (tenant_id, profile_id, index_id))
        profiles = metadata.tables['embedding_profiles']
        indexes = metadata.tables['knowledge_index_versions']
        with self.engine.begin() as db:
            self._guard(db)
            model = db.execute(sa.select(profiles).where(profiles.c.tenant_id == tenant,
                profiles.c.profile_id == profile)).mappings().first()
            if not model or (model['model_id'], model['model_revision'], model['weights_sha256'],
                model['license_id'], model['dimension'], model['distance'], model['normalized']) != (
                    EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                    EMBEDDING_WEIGHT.sha256, EMBEDDING_WEIGHT.license_id,
                    1024, 'cosine', True):
                raise ValueError('Existing profile is not the pinned local model')
            db.execute(indexes.insert().values(tenant_id=tenant, index_id=index,
                profile_id=profile, status='building', lexical_version=lexical_version,
                chunker_version=chunker_version, reranker_version=reranker_version,
                created_at=datetime.now(timezone.utc)))
            self._audit(db,tenant,index,'stage_existing',{'profile_id':str(profile)})

    def _manifest(self, db, index):
        tenant, profile = index['tenant_id'], index['profile_id']
        profiles = metadata.tables['embedding_profiles']
        model = db.execute(sa.select(profiles).where(profiles.c.tenant_id == tenant,
            profiles.c.profile_id == profile)).mappings().one()
        if (model['model_id'], model['model_revision'], model['weights_sha256'],
            model['license_id'], model['dimension'], model['distance'], model['normalized']) != (
                EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                EMBEDDING_WEIGHT.sha256, EMBEDDING_WEIGHT.license_id,
                1024, 'cosine', True):
            raise ValueError('Index profile is not the pinned local model')
        # A ready document must have every current chunk snapshot before it
        # can be indexed. This catches migrations that omitted backfill.
        missing_snapshots = db.execute(sa.text("""
            SELECT d.id FROM documents d WHERE d.tenant_id=:tenant AND d.status='ready'
              AND (d.chunk_count != (SELECT count(*) FROM knowledge_chunks c
                WHERE c.tenant_id=d.tenant_id AND c.document_id=d.id)
              OR d.chunk_count != (SELECT count(*) FROM knowledge_chunk_versions v
                WHERE v.tenant_id=d.tenant_id AND v.document_id=d.id
                  AND v.document_version=d.version)
              OR EXISTS (SELECT 1 FROM knowledge_chunks c
                LEFT JOIN knowledge_chunk_versions v ON v.tenant_id=c.tenant_id
                  AND v.document_id=c.document_id AND v.document_version=d.version
                  AND v.chunk_index=c.chunk_index
                WHERE c.tenant_id=d.tenant_id AND c.document_id=d.id
                  AND (v.chunk_index IS NULL OR v.content != c.content)))
            LIMIT 1"""), {'tenant': tenant}).first()
        if missing_snapshots:
            raise ValueError('Current document snapshots are incomplete or inconsistent')
        missing_vectors = db.execute(sa.text("""
            SELECT v.document_id FROM knowledge_chunk_versions v JOIN documents d
              ON d.tenant_id=v.tenant_id AND d.id=v.document_id
            WHERE v.tenant_id=:tenant AND d.status='ready' AND d.version=v.document_version
              AND NOT EXISTS (SELECT 1 FROM knowledge_embeddings_1024 e
                WHERE e.tenant_id=v.tenant_id AND e.profile_id=:profile
                  AND e.document_id=v.document_id AND e.document_version=v.document_version
                  AND e.chunk_index=v.chunk_index AND e.content_sha256=v.content_sha256)
            LIMIT 1"""), {'tenant': tenant, 'profile': profile}).first()
        if missing_vectors:
            raise ValueError('Current document Embeddings are incomplete')
        digest = hashlib.sha256()
        for item in (str(tenant), str(profile), model['model_id'],
                     model['model_revision'], model['weights_sha256'], model['license_id'],
                     str(model['dimension']), model['distance'], str(model['normalized']),
                     str(model['max_tokens']), index['lexical_version'],
                     index['chunker_version'], index['reranker_version'] or ''):
            digest.update(item.encode('utf-8') + b'\0')
        rows = db.execute(sa.text("""
            SELECT v.document_id,v.document_version,v.chunk_index,v.content_sha256,v.content
            FROM knowledge_chunk_versions v JOIN documents d
              ON d.tenant_id=v.tenant_id AND d.id=v.document_id
            WHERE v.tenant_id=:tenant AND d.status='ready' AND d.version=v.document_version
            ORDER BY v.document_id,v.document_version,v.chunk_index"""),
            {'tenant': tenant}).yield_per(1000)
        count = 0
        for row in rows:
            if hashlib.sha256(row.content.encode('utf-8')).hexdigest() != row.content_sha256:
                raise ValueError('Current RAG snapshot content hash mismatch')
            for value in row[:4]:
                digest.update(str(value).encode('ascii') + b'\0')
            count += 1
        return digest.hexdigest(), count

    def seal(self, *, tenant_id, index_id):
        tenant, index_key = uuid.UUID(str(tenant_id)), uuid.UUID(str(index_id))
        indexes = metadata.tables['knowledge_index_versions']
        with self.engine.begin() as db:
            self._guard(db)
            row = db.execute(sa.select(indexes).where(indexes.c.tenant_id == tenant,
                indexes.c.index_id == index_key).with_for_update()).mappings().first()
            if not row or row['status'] != 'building':
                raise ValueError('Only a building RAG index can be sealed')
            manifest, count = self._manifest(db, row)
            db.execute(indexes.update().where(indexes.c.tenant_id == tenant,
                indexes.c.index_id == index_key).values(status='ready', manifest_sha256=manifest))
            self._audit(db,tenant,index_key,'seal',{'manifest_sha256':manifest,'current_chunks':count})
            return {'manifest_sha256': manifest, 'current_chunks': count}

    def activate(self, *, tenant_id, index_id, expected_active_index_id=_UNSET):
        with self.engine.begin() as db:
            return self._activate(db, tenant_id=tenant_id, index_id=index_id,
                                  expected_active_index_id=expected_active_index_id)

    def _activate(self, db, *, tenant_id, index_id, expected_active_index_id=_UNSET):
        """Internal transaction-sharing activation; caller owns commit/rollback."""
        tenant, index_key = uuid.UUID(str(tenant_id)), uuid.UUID(str(index_id))
        indexes = metadata.tables['knowledge_index_versions']
        with db.begin_nested():
            self._guard(db)
            if expected_active_index_id is _UNSET:
                raise ValueError('Explicit expected active RAG index is required')
            current = db.execute(sa.select(indexes.c.index_id).where(
                indexes.c.tenant_id == tenant, indexes.c.status == 'active')).scalar_one_or_none()
            expected = (uuid.UUID(str(expected_active_index_id))
                        if expected_active_index_id is not None else None)
            if current != expected:
                raise ValueError('Active RAG index changed since rollout approval')
            row = db.execute(sa.select(indexes).where(indexes.c.tenant_id == tenant,
                indexes.c.index_id == index_key).with_for_update()).mappings().first()
            if not row or row['status'] != 'ready':
                raise ValueError('Only a ready RAG index can be activated')
            manifest, count = self._manifest(db, row)
            if manifest != row['manifest_sha256']:
                raise ValueError('RAG index changed after sealing; stage a new index')
            db.execute(indexes.update().where(indexes.c.tenant_id == tenant,
                indexes.c.status == 'active').values(status='retired'))
            db.execute(indexes.update().where(indexes.c.tenant_id == tenant,
                indexes.c.index_id == index_key).values(status='active',
                    activated_at=datetime.now(timezone.utc)))
            self._audit(db,tenant,index_key,'activate',{'previous_index_id':str(current) if current else None,
                'profile_id':str(row['profile_id']),'manifest_sha256':manifest,'current_chunks':count})
            return {'manifest_sha256': manifest, 'current_chunks': count}

    def shadow_compare(self, *, tenant_id, index_id, vectors, top_k=3, max_tokens):
        """Compare a ready index with the active index without changing routing.

        The input consists only of locally generated query vectors. No query
        text, candidate text, or document identifiers leave this method.
        """
        if not 1 <= len(vectors) <= 100 or type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError('Shadow comparison requires 1-100 vectors and top_k 1-20')
        if any(len(vector) != 1024 or not all(isinstance(value, (int, float)) and
                not isinstance(value, bool) and math.isfinite(value) for value in vector)
                or not 0.99 <= math.sqrt(sum(float(value) ** 2 for value in vector)) <= 1.01
                for vector in vectors):
            raise ValueError('Shadow query vectors must be finite normalized 1024-D values')
        tenant, candidate = uuid.UUID(str(tenant_id)), uuid.UUID(str(index_id))
        indexes = metadata.tables['knowledge_index_versions']
        profiles = metadata.tables['embedding_profiles']
        query = sa.text('''SELECT e.document_id,e.document_version,e.chunk_index,e.content_sha256
            FROM knowledge_embeddings_1024 e
            JOIN documents d ON d.tenant_id=e.tenant_id AND d.id=e.document_id
            JOIN knowledge_chunk_versions v ON v.tenant_id=e.tenant_id
              AND v.document_id=e.document_id AND v.document_version=e.document_version
              AND v.chunk_index=e.chunk_index AND v.content_sha256=e.content_sha256
            JOIN knowledge_chunks c ON c.tenant_id=e.tenant_id AND c.document_id=e.document_id
              AND c.chunk_index=e.chunk_index AND c.content=v.content
            WHERE e.tenant_id=:tenant AND e.profile_id=:profile
              AND d.status='ready' AND d.version=e.document_version
            ORDER BY e.embedding <=> CAST(:vector AS vector),e.document_id,e.chunk_index
            LIMIT :limit''')
        with self.engine.begin() as db:
            # Read-only shadow work may be expensive. Share the publication
            # lock with answer commits, but still serialize against activation.
            self._guard(db, shared=True)
            rows = db.execute(sa.select(indexes, profiles.c.model_id, profiles.c.model_revision,
                profiles.c.weights_sha256, profiles.c.license_id, profiles.c.dimension,
                profiles.c.distance, profiles.c.normalized, profiles.c.max_tokens).join(profiles,
                sa.and_(profiles.c.tenant_id == indexes.c.tenant_id,
                        profiles.c.profile_id == indexes.c.profile_id)).where(
                indexes.c.tenant_id == tenant,
                sa.or_(indexes.c.status == 'active', indexes.c.index_id == candidate))).mappings().all()
            active = next((row for row in rows if row['status'] == 'active'), None)
            shadow = next((row for row in rows if row['index_id'] == candidate and row['status'] == 'ready'), None)
            if active is None or shadow is None:
                raise ValueError('Shadow comparison requires an active and a distinct ready index')
            for row in (active, shadow):
                if (row['model_id'], row['model_revision'], row['weights_sha256'],
                    row['license_id'], row['dimension'], row['distance'], row['normalized'],
                    row['max_tokens']) != (EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                    EMBEDDING_WEIGHT.sha256, EMBEDDING_WEIGHT.license_id, 1024, 'cosine', True,
                    max_tokens):
                    raise ValueError('Shadow profile does not match the pinned local Embedding model')
            manifest, _ = self._manifest(db, shadow)
            if manifest != shadow['manifest_sha256']:
                raise ValueError('Shadow comparison ready index manifest changed')
            overlap, changed_top1, active_empty, shadow_empty = 0, 0, 0, 0
            for vector in vectors:
                encoded = '[' + ','.join(format(float(value), '.9g') for value in vector) + ']'
                def top(profile_id):
                    return [(row.document_id, row.document_version, row.chunk_index,
                             row.content_sha256) for row in db.execute(query, {
                        'tenant': tenant, 'profile': profile_id, 'vector': encoded,
                        'limit': top_k})]
                baseline = top(active['profile_id'])
                candidate_rows = top(shadow['profile_id'])
                overlap += len(set(baseline) & set(candidate_rows))
                changed_top1 += bool(baseline and candidate_rows and baseline[0] != candidate_rows[0])
                active_empty += not baseline
                shadow_empty += not candidate_rows
            count = len(vectors)
            return {'count': count, 'top_k': top_k, 'mean_overlap_at_k': overlap / (count * top_k),
                    'top1_changed': changed_top1, 'active_empty': active_empty,
                    'shadow_empty': shadow_empty,
                    'active_index_id': str(active['index_id']), 'ready_index_id': str(candidate)}

    def prune_stale_embeddings(self, *, tenant_id, batch_size=1000):
        """Delete one bounded batch no longer eligible for current retrieval.

        Retired profiles remain intact for rollback; only vectors for deleted,
        non-ready, or superseded document versions are eligible. Snapshot rows
        and audit/history are not removed by this operation.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Invalid RAG prune batch size')
        tenant = uuid.UUID(str(tenant_id))
        with self.engine.begin() as db:
            self._guard(db)
            rows = db.execute(sa.text("""
                WITH victims AS (
                    SELECT e.ctid FROM public.knowledge_embeddings_1024 e
                    WHERE e.tenant_id=:tenant
                      AND NOT EXISTS (
                        SELECT 1 FROM public.documents d
                        WHERE d.tenant_id=e.tenant_id AND d.id=e.document_id
                          AND d.status='ready' AND d.version=e.document_version
                      )
                    ORDER BY e.profile_id,e.document_id,e.document_version,e.chunk_index
                    LIMIT :batch_size FOR UPDATE OF e SKIP LOCKED
                )
                DELETE FROM public.knowledge_embeddings_1024 e USING victims v
                WHERE e.ctid=v.ctid AND e.tenant_id=:tenant
                RETURNING e.profile_id
            """), {'tenant': tenant, 'batch_size': batch_size}).all()
            if rows:
                self._audit(db,tenant,tenant,'prune_stale',{'deleted_count':len(rows),'batch_size':batch_size})
            return len(rows)
