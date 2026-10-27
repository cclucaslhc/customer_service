"""Pinned 1024-dimensional pgvector storage; extension is a DBA prerequisite."""

from alembic import context, op
import sqlalchemy as sa

from app.persistence.schema_v1 import metadata as baseline
from app.persistence.rag_vector_v1 import add_rag_vectors

revision = '0028_rag_vector'
down_revision = '0027_rag_chunk_versions'
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    if context.is_offline_mode():
        op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') "
                   "THEN RAISE EXCEPTION 'DBA must install pgvector before 0028'; END IF; END $$")
    else:
        installed = db.exec_driver_sql("SELECT extversion FROM pg_extension WHERE extname='vector'").scalar_one_or_none()
        if not installed:
            raise RuntimeError('DBA must install the trusted pgvector extension before 0028; migration does not create extensions')
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('embedding_profiles', meta, sa.Column('tenant_id', sa.Uuid()),
             sa.Column('profile_id', sa.Uuid()))
    sa.Table('knowledge_chunk_versions', meta,
             sa.Column('tenant_id', sa.Uuid()), sa.Column('document_id', sa.Uuid()),
             sa.Column('document_version', sa.Integer()), sa.Column('chunk_index', sa.Integer()),
             sa.Column('content_sha256', sa.String(64)))
    table = add_rag_vectors(meta)
    table.create(db, checkfirst=False)
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
               'ON public.knowledge_embeddings_1024 FOR EACH STATEMENT '
               'EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('RAG vector downgrade is disabled: preserve versioned embedding evidence')
