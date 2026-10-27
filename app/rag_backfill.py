"""Restartable current-version chunk snapshot backfill for migrated PostgreSQL data."""

import hashlib
from datetime import datetime, timezone

import sqlalchemy as sa

from app.persistence.schema import metadata


_MISSING = sa.text("""
SELECT d.tenant_id,d.id AS document_id,d.version AS document_version,
       d.parser_version,d.chunker_version,c.chunk_index,c.content,c.section,
       c.location,c.block_type,c.metadata_json
FROM documents d JOIN knowledge_chunks c
  ON c.tenant_id=d.tenant_id AND c.document_id=d.id
WHERE d.status='ready' AND NOT EXISTS (
  SELECT 1 FROM knowledge_chunk_versions v
  WHERE v.tenant_id=d.tenant_id AND v.document_id=d.id
    AND v.document_version=d.version AND v.chunk_index=c.chunk_index)
ORDER BY d.id,d.version,c.chunk_index
LIMIT :limit FOR SHARE OF d SKIP LOCKED
""")


def backfill_current_chunks(engine, *, batch_size: int = 100) -> int:
    if engine.dialect.name != 'postgresql' or batch_size < 1 or batch_size > 1000:
        raise ValueError('RAG backfill requires PostgreSQL and a bounded batch')
    table = metadata.tables['knowledge_chunk_versions']
    total = 0
    while True:
        with engine.begin() as db:
            rows = db.execute(_MISSING, {'limit': batch_size}).mappings().all()
            if not rows:
                return total
            now = datetime.now(timezone.utc)
            db.execute(table.insert(), [{
                **dict(row),
                'content_sha256': hashlib.sha256(row['content'].encode('utf-8')).hexdigest(),
                'created_at': now,
            } for row in rows])
            total += len(rows)
