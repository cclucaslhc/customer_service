"""Knowledge metadata persistence; lexical adapters are not vector retrieval."""

import hashlib
from copy import deepcopy
import math
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.persistence.base import LocalRepository, Repository, wire
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID


class KnowledgeWriteConflict(ValueError):
    pass


class KnowledgeRepository(Repository):
    table_names = ("documents", "knowledge_chunks", "knowledge_assets", "knowledge_jobs", "app_settings")

    def __init__(self, engine, *, token=None):
        super().__init__(engine)
        if token is not None and engine.dialect.name != "postgresql":
            raise ValueError("Authenticated knowledge requires PostgreSQL")
        self._token = token

    def authenticated(self, token):
        from app.persistence.identity_repository import IdentityRepository
        IdentityRepository(self.engine).resolve(token)
        return type(self)(self.engine, token=token)

    def with_route(self,route):
        """Internal per-run clone, never mutate a shared service repository."""
        fields={'tenant_id','session_id','schema_version','rollout_id','release_id','route_version','index_id'}
        if (not isinstance(route,dict) or set(route) not in (fields,fields|{'model_config_sha256','prompt_sha256'}) or type(route['schema_version']) is not int
                or route['schema_version']!=1 or (route['route_version'] is not None
                    and type(route['route_version']) is not int)):
            raise ValueError('Invalid internal RAG route snapshot')
        clone=type(self)(self.engine,token=self._token)
        clone._rag_route=deepcopy(route)
        return clone

    def _route_guard(self,db,principal):
        route=getattr(self,'_rag_route',None)
        if route is None:
            return None
        from app.rag_routing import current_route,context_from_route
        from app.knowledge_retrieval import KnowledgeIndexChanged
        if (principal is None or principal.tenant_id!=route['tenant_id']
                or current_route(db,credential=self._token,tenant_id=principal.tenant_id,
                    session_id=route['session_id'],lock_identities=True,
                    runtime_context=context_from_route(route))!=route):
            raise KnowledgeIndexChanged()
        return route

    def _principal(self, db, permission="knowledge.read"):
        if self._token is None:
            return None
        from app.persistence.identity_repository import IdentityRepository
        principal = IdentityRepository(self.engine).resolve(self._token, connection=db)
        principal.require(permission)
        return principal

    def _scope(self, db, table, permission="knowledge.read"):
        principal = self._principal(db, permission)
        if "tenant_id" not in table.c:
            return sa.true()
        tenant = principal.tenant_id if principal else LEGACY_TENANT_ID
        return table.c.tenant_id == self.values(table, tenant_id=tenant)["tenant_id"]

    def _ownership(self, db, table, actor=None):
        principal = self._principal(db, "knowledge.manage")
        if "tenant_id" not in table.c:
            return {}
        fields = {"tenant_id": principal.tenant_id if principal else LEGACY_TENANT_ID}
        if actor:
            fields[actor] = principal.user_id if principal else None
        return self.values(table, **fields)

    def require_manage(self):
        with self.engine.connect() as db:
            from app.recovery import require_operational
            require_operational(db)
            self._principal(db, "knowledge.manage")

    def storage_namespace(self):
        if self._token is None:
            return None
        with self.engine.connect() as db:
            return self._principal(db).tenant_id

    def lookup_ingest(self, source_path, digest):
        d = self.tables["documents"]
        with self.engine.connect() as db:
            scope = self._scope(db, d, "knowledge.manage")
            existing = wire(db.execute(sa.select(d).where(d.c.source_path == source_path, scope).order_by(d.c.version.desc()).limit(1)).mappings().first()) if source_path else None
            duplicate = wire(db.execute(sa.select(d.c.id, d.c.source_path).where(d.c.sha256 == digest, scope)).mappings().first())
            return existing, duplicate

    def publish_document(self, document, chunks, assets, expected_version=None):
        d, c, a = (self.tables[name] for name in ("documents", "knowledge_chunks", "knowledge_assets"))
        if any(set(row) & {"tenant_id", "created_by", "requested_by"} for row in [document, *chunks, *assets]):
            raise ValueError("Publication ownership is server controlled")
        if document.get("chunk_count") != len(chunks) or any(str(row.get("document_id")) != str(document["id"]) for row in [*chunks, *assets]):
            raise ValueError("Invalid publication manifest")
        if [row.get("chunk_index") for row in chunks] != list(range(len(chunks))):
            raise ValueError("Invalid chunk order")
        # Serializes the short publication transaction, not parsing or OCR.
        with self.engine.begin() as db:
            scope = self._scope(db, d, "knowledge.manage")
            self.serialize_writes(db, 1129533503)
            self._publication_guard(db,document)
            key = self.key(d, document["id"])
            current = db.execute(sa.select(d.c.version).where(d.c.id == key, scope)).scalar_one_or_none()
            if current is None and db.execute(sa.select(d.c.id).where(d.c.id == key)).first():
                raise KnowledgeWriteConflict("Document unavailable")
            if current != expected_version:
                raise KnowledgeWriteConflict("Document changed during parsing; retry with current version")
            if document.get("version") != (current or 0) + 1:
                raise ValueError("Invalid document version")
            source_path = document.get("source_path")
            if source_path:
                other = db.execute(sa.select(d.c.id).where(d.c.source_path == source_path, d.c.id != key, scope)).first()
                if other:
                    raise KnowledgeWriteConflict("Source was imported concurrently; retry")
            if db.execute(sa.select(d.c.id).where(d.c.sha256 == document["sha256"], d.c.id != key, scope)).first():
                raise KnowledgeWriteConflict("Content was imported concurrently; retry")
            values = self.values(d, **document)
            values.update(self._ownership(db, d, "created_by"))
            if current is None:
                db.execute(d.insert().values(**values))
            else:
                values.pop("created_at", None)
                values.pop("created_by", None)
                db.execute(c.delete().where(c.c.document_id == key))
                db.execute(a.delete().where(a.c.document_id == key))
                db.execute(d.update().where(d.c.id == key, scope).values(**values))
            if chunks:
                ownership = self._ownership(db, c)
                db.execute(c.insert(), [{**self.values(c, **row), **ownership} for row in chunks])
                if self.engine.dialect.name == 'postgresql':
                    snapshots = self.tables['knowledge_chunk_versions']
                    snapshot_owner = self._ownership(db, snapshots)
                    now = datetime.now(timezone.utc)
                    db.execute(snapshots.insert(), [
                        {**self.values(snapshots,
                            document_id=document['id'], document_version=document['version'],
                            chunk_index=row['chunk_index'], content_sha256=hashlib.sha256(
                                row['content'].encode('utf-8')).hexdigest(), content=row['content'],
                            section=row.get('section'), location=row.get('location'),
                            block_type=row.get('block_type') or 'paragraph',
                            metadata_json=row.get('metadata_json') or {},
                            parser_version=document.get('parser_version') or '1.0',
                            chunker_version=document.get('chunker_version') or '1.0',
                            created_at=now), **snapshot_owner}
                        for row in chunks
                    ])
            if assets:
                ownership = self._ownership(db, a)
                db.execute(a.insert(), [{**self.values(a, **row), **ownership} for row in assets])
            self._publication_committed(db,document)

    def _publication_guard(self, db, document):
        from app.recovery import require_operational
        require_operational(db)

    def _publication_committed(self, db, document):
        pass

    def document(self, document_id):
        d = self.tables["documents"]
        with self.engine.connect() as db:
            return wire(db.execute(sa.select(d).where(d.c.id == self.key(d, document_id), self._scope(db, d, "knowledge.manage"))).mappings().first())

    def list_documents(self):
        d, a = self.tables["documents"], self.tables["knowledge_assets"]
        count = sa.select(sa.func.count()).where(a.c.document_id == d.c.id).correlate(d).scalar_subquery()
        with self.engine.connect() as db:
            return [wire(row) for row in db.execute(sa.select(d, count.label("asset_count")).where(self._scope(db, d, "knowledge.manage")).order_by(d.c.created_at.desc(), d.c.id)).mappings()]

    def document_chunks(self, document_id):
        c = self.tables["knowledge_chunks"]
        key = self.values(c, document_id=document_id)["document_id"]
        with self.engine.connect() as db:
            query = sa.select(c.c.id, c.c.chunk_index, c.c.section, c.c.location, c.c.block_type, c.c.char_count, c.c.metadata_json,
                              sa.func.substr(c.c.content, 1, 1200).label("content")).where(c.c.document_id == key, self._scope(db, c, "knowledge.manage")).order_by(c.c.chunk_index)
            return [wire(row) for row in db.execute(query).mappings()]

    def delete_document(self, document_id, expected_version):
        d = self.tables["documents"]
        with self.engine.begin() as db:
            scope = self._scope(db, d, "knowledge.manage")
            self.serialize_writes(db, 1129533503)
            return bool(db.execute(d.delete().where(d.c.id == self.key(d, document_id), d.c.version == expected_version, scope)).rowcount)

    def assets(self, document_ids):
        if not document_ids:
            return []
        a = self.tables["knowledge_assets"]
        keys = [self.values(a, document_id=value)["document_id"] for value in document_ids]
        with self.engine.connect() as db:
            return [wire(row) for row in db.execute(sa.select(a).where(a.c.document_id.in_(keys), self._scope(db, a)).order_by(a.c.asset_index, a.c.id)).mappings()]

    def asset(self, asset_id):
        a, d = self.tables["knowledge_assets"], self.tables["documents"]
        with self.engine.connect() as db:
            return wire(db.execute(sa.select(a).join(d, d.c.id == a.c.document_id).where(a.c.id == self.key(a, asset_id), d.c.status == "ready", self._scope(db, d))).mappings().first())

    def setting(self, name):
        s = self.tables["knowledge_settings"] if self.engine.dialect.name == "postgresql" else self.tables["app_settings"]
        with self.engine.connect() as db:
            return db.execute(sa.select(s.c.value).where(s.c.key == name, self._scope(db, s))).scalar_one_or_none()

    def _set_setting(self, db, name, value):
        s = self.tables["knowledge_settings"] if self.engine.dialect.name == "postgresql" else self.tables["app_settings"]
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        db.execute(insert(s).values(key=name, value=value, **self._ownership(db, s)).on_conflict_do_update(index_elements=list(s.primary_key.columns), set_={"value": value}))

    def set_setting(self, name, value):
        with self.engine.begin() as db:
            self._principal(db, "knowledge.manage")
            self._set_setting(db, name, value)

    def rebuild(self, tokenize, tokenizer_version):
        c = self.tables["knowledge_chunks"]
        # Tokenization stays outside the transaction; compare content on update.
        with self.engine.connect() as db:
            rows = db.execute(sa.select(c.c.id, c.c.content).where(self._scope(db, c, "knowledge.manage"))).all()
        prepared = [(row.id, row.content, tokenize(row.content)) for row in rows]
        with self.engine.begin() as db:
            scope = self._scope(db, c, "knowledge.manage")
            self.serialize_writes(db, 1129533503)
            for key, content, tokens in prepared:
                db.execute(c.update().where(c.c.id == key, c.c.content == content, scope).values(tokenized=tokens))
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
            self._set_setting(db, "knowledge_tokenizer_version", tokenizer_version)
        return len(prepared)

    def search_candidates(self, terms, limit):
        c, d = self.tables["knowledge_chunks"], self.tables["documents"]
        columns = [c.c.id, c.c.content, c.c.section, c.c.location, c.c.block_type, c.c.metadata_json, d.c.id.label("document_id"), d.c.display_name]
        if self.engine.dialect.name == 'postgresql':
            columns.extend((d.c.tenant_id, d.c.version.label('document_version'), c.c.chunk_index))
        with self.engine.connect() as db:
            if self.engine.dialect.name == "sqlite":
                query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:64])
                sql = sa.text("""SELECT c.id,c.content,c.section,c.location,c.block_type,c.metadata_json,d.id document_id,d.display_name,
                            bm25(knowledge_fts) score FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.rowid
                            JOIN documents d ON d.id=c.document_id WHERE knowledge_fts MATCH :query AND d.status='ready'
                            ORDER BY score,c.id LIMIT :limit""")
                lexical = [wire(row) for row in db.execute(sql, {"query": query, "limit": limit}).mappings()]
                kind = "sqlite_bm25"
            else:
                # PostgreSQL's ts_rank is not BM25. Existing space-separated
                # Chinese/model tokens use simple configuration until S6 upgrade.
                text_query = " OR ".join('"' + term.replace('"', '') + '"' for term in terms[:64])
                vector = sa.func.to_tsvector(sa.literal_column("'simple'::regconfig"), c.c.tokenized)
                tsquery = sa.func.websearch_to_tsquery(sa.literal_column("'simple'::regconfig"), text_query)
                score = -sa.func.ts_rank_cd(vector, tsquery)
                query = sa.select(*columns, score.label("score")).join(d, d.c.id == c.c.document_id).where(d.c.status == "ready", vector.op("@@")(tsquery), self._scope(db, d)).order_by(score, c.c.id).limit(limit)
                lexical = [wire(row) for row in db.execute(query).mappings()]
                kind = "postgres_ts_rank"
            for row in lexical:
                row["score_kind"] = kind
            fallback = [wire(row) for row in db.execute(sa.select(*columns).join(d, d.c.id == c.c.document_id).where(d.c.status == "ready", self._scope(db, d)).order_by(c.c.id.desc()).limit(5000)).mappings()]
        return lexical, fallback

    def store_embedding(self, *, profile_id, document_id, document_version,
                        chunk_index, content_sha256, vector):
        """Idempotent, tenant-bound 1024-d snapshot embedding publication."""
        if self.engine.dialect.name != 'postgresql' or len(vector) != 1024 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in vector):
            raise ValueError('Invalid pgvector embedding')
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not 0.99 <= norm <= 1.01 or len(content_sha256) != 64:
            raise ValueError('Embedding must be normalized and match immutable content')
        from app.rag_model_manifest import EMBEDDING_WEIGHT
        profiles = self.tables['embedding_profiles']
        snapshots = self.tables['knowledge_chunk_versions']
        embeddings = self.tables['knowledge_embeddings_1024']
        with self.engine.begin() as db:
            principal = self._principal(db, 'knowledge.manage')
            tenant = uuid.UUID(principal.tenant_id if principal else LEGACY_TENANT_ID)
            profile = db.execute(sa.select(profiles).where(
                profiles.c.tenant_id == tenant, profiles.c.profile_id == uuid.UUID(str(profile_id)))).mappings().first()
            if not profile or (profile['model_id'], profile['model_revision'], profile['weights_sha256'],
                               profile['dimension'], profile['distance'], profile['normalized']) != (
                                   EMBEDDING_WEIGHT.repository, EMBEDDING_WEIGHT.revision,
                                   EMBEDDING_WEIGHT.sha256, 1024, 'cosine', True):
                raise ValueError('Embedding profile is not the pinned local model')
            identity = dict(tenant_id=tenant, document_id=uuid.UUID(str(document_id)),
                            document_version=document_version, chunk_index=chunk_index,
                            content_sha256=content_sha256)
            source = db.execute(sa.select(snapshots.c.content).where(
                    *(snapshots.c[name] == value for name, value in identity.items()))
                    .with_for_update()).scalar_one_or_none()
            if source is None:
                raise ValueError('Embedding source snapshot is unavailable')
            if hashlib.sha256(source.encode('utf-8')).hexdigest() != content_sha256:
                raise ValueError('Embedding source snapshot content hash mismatch')
            row = {**identity, 'profile_id': uuid.UUID(str(profile_id)),
                   'embedding': [float(value) for value in vector],
                   'embedded_at': datetime.now(timezone.utc)}
            return bool(db.execute(pg_insert(embeddings).values(**row)
                .on_conflict_do_nothing().returning(embeddings.c.chunk_index)).first())

    def search_vector_candidates(self, vector, *, profile_id, limit):
        """Exact pgvector search after tenant/profile/current-version filtering."""
        if self.engine.dialect.name != 'postgresql' or not 1 <= limit <= 100 or len(vector) != 1024 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in vector):
            raise ValueError('Invalid pgvector search')
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not 0.99 <= norm <= 1.01:
            raise ValueError('Query vector must be normalized')
        with self.engine.connect() as db:
            principal = self._principal(db)
            tenant = uuid.UUID(principal.tenant_id if principal else LEGACY_TENANT_ID)
            route=self._route_guard(db,principal)
            sql = sa.text('''WITH scoped AS MATERIALIZED (
                SELECT e.tenant_id,e.document_id,e.document_version,e.chunk_index,e.content_sha256,e.embedding,
                       c.id,
                       v.content,v.section,v.location,v.block_type,v.metadata_json,d.display_name
                FROM knowledge_embeddings_1024 e
                JOIN knowledge_chunk_versions v ON v.tenant_id=e.tenant_id
                  AND v.document_id=e.document_id AND v.document_version=e.document_version
                  AND v.chunk_index=e.chunk_index AND v.content_sha256=e.content_sha256
                JOIN documents d ON d.tenant_id=e.tenant_id AND d.id=e.document_id
                JOIN knowledge_chunks c ON c.tenant_id=e.tenant_id AND c.document_id=e.document_id
                  AND c.chunk_index=e.chunk_index AND c.content=v.content
                JOIN knowledge_index_versions i ON i.tenant_id=e.tenant_id AND i.profile_id=e.profile_id
                WHERE e.tenant_id=:tenant AND e.profile_id=:profile
                  AND ((:routed AND i.index_id=:selected_index AND i.status IN ('active','ready'))
                    OR (NOT :routed AND i.status='active'))
                  AND d.status='ready' AND d.version=e.document_version
            ) SELECT tenant_id,id,document_id,document_version,chunk_index,content_sha256,content,section,
                     location,block_type,metadata_json,display_name,
                     embedding <=> CAST(:query AS vector) AS distance
              FROM scoped ORDER BY distance,document_id,chunk_index LIMIT :limit''')
            query = '[' + ','.join(format(float(value), '.9g') for value in vector) + ']'
            return [wire(row) for row in db.execute(sql, {'tenant': tenant,
                'routed':route is not None,'selected_index':uuid.UUID(route['index_id']) if route and route['index_id'] else None,
                'profile': uuid.UUID(str(profile_id)), 'query': query, 'limit': limit}).mappings()]

    def active_vector_profile(self):
        if self.engine.dialect.name != 'postgresql':
            return None
        profiles = self.tables['embedding_profiles']
        indexes = self.tables['knowledge_index_versions']
        with self.engine.connect() as db:
            principal = self._principal(db)
            tenant = uuid.UUID(principal.tenant_id if principal else LEGACY_TENANT_ID)
            route=self._route_guard(db,principal)
            selected=(sa.and_(indexes.c.index_id==uuid.UUID(route['index_id']),
                indexes.c.status.in_(('active','ready'))) if route and route['index_id'] else
                sa.false() if route else indexes.c.status=='active')
            row = db.execute(sa.select(profiles, indexes.c.index_id, indexes.c.lexical_version,
                indexes.c.chunker_version, indexes.c.reranker_version,
                indexes.c.manifest_sha256).join(indexes,
                sa.and_(indexes.c.tenant_id == profiles.c.tenant_id,
                        indexes.c.profile_id == profiles.c.profile_id)).where(
                    indexes.c.tenant_id == tenant, selected)).mappings().first()
            return wire(row)

    def filter_current_results(self, results):
        """Recheck scoped document/version/content immediately before disclosure."""
        if not results or self.engine.dialect.name != 'postgresql':
            return results
        chunks = self.tables['knowledge_chunks']
        documents = self.tables['documents']
        with self.engine.connect() as db:
            rows = db.execute(sa.select(chunks.c.id, chunks.c.content,
                documents.c.version).join(documents,
                    sa.and_(documents.c.id == chunks.c.document_id,
                            documents.c.tenant_id == chunks.c.tenant_id)).where(
                chunks.c.id.in_([item['id'] for item in results]),
                documents.c.status == 'ready', self._scope(db, documents))).all()
        current = {row.id: (row.version, hashlib.sha256(row.content.encode('utf-8')).hexdigest())
                   for row in rows}
        return [item for item in results if current.get(item['id']) == (
            item['document_version'], item.get('content_sha256') or
            hashlib.sha256(item['content'].encode('utf-8')).hexdigest())]

    def create_job(self, values):
        j = self.tables["knowledge_jobs"]
        if set(values) & {"tenant_id", "requested_by"}:
            raise ValueError("Job ownership is server controlled")
        with self.engine.begin() as db:
            db.execute(j.insert().values(**self.values(j, **values), **self._ownership(db, j, "requested_by")))

    def job(self, job_id):
        j = self.tables["knowledge_jobs"]
        with self.engine.connect() as db:
            return wire(db.execute(sa.select(j).where(j.c.id == self.key(j, job_id), self._scope(db, j, "knowledge.manage"))).mappings().first())

    def jobs(self, limit):
        j = self.tables["knowledge_jobs"]
        with self.engine.connect() as db:
            priority = sa.case((j.c.status == 'running',0),(j.c.status == 'pending',1),else_=2)
            return [wire(row) for row in db.execute(sa.select(j).where(self._scope(db, j, "knowledge.manage")).order_by(priority,j.c.created_at.desc(), j.c.id).limit(limit)).mappings()]

    def update_job(self, job_id, values):
        j = self.tables["knowledge_jobs"]
        allowed = {"status", "stage", "total_count", "processed_count", "imported_count", "skipped_count", "failed_count", "current_file", "result_json", "error_message", "started_at", "finished_at"}
        if set(values) - allowed:
            raise ValueError("Invalid import job fields")
        if values:
            with self.engine.begin() as db:
                condition = j.c.execution_mode == 'local' if self.engine.dialect.name == 'postgresql' else sa.true()
                return bool(db.execute(j.update().where(j.c.id == self.key(j, job_id), condition, self._scope(db, j, "knowledge.manage")).values(**self.values(j, **values))).rowcount)

    def recover_local_jobs(self):
        if self.engine.dialect.name != "sqlite":
            raise RuntimeError("Global startup recovery is local-only; PostgreSQL requires Worker leases")
        j = self.tables["knowledge_jobs"]
        with self.engine.begin() as db:
            self.serialize_writes(db, 1129533503)
            ids = list(db.execute(sa.select(j.c.id).where(j.c.status.in_(["pending", "running"])).order_by(j.c.created_at)).scalars())
            db.execute(j.update().where(j.c.status == "running").values(status="pending", stage="pending", current_file=None, error_message=None))
            return ids


class LocalKnowledgeRepository(LocalRepository):
    repository_type = KnowledgeRepository
