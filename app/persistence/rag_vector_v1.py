"""pgvector 1024-dimensional profile storage and scoped ANN index."""

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR


def add_rag_vectors(meta):
    vector_type = VECTOR(1024).with_variant(sa.JSON(), 'sqlite')
    table = sa.Table('knowledge_embeddings_1024', meta,
        sa.Column('tenant_id', sa.Uuid(), primary_key=True),
        sa.Column('profile_id', sa.Uuid(), primary_key=True),
        sa.Column('document_id', sa.Uuid(), primary_key=True),
        sa.Column('document_version', sa.Integer(), primary_key=True),
        sa.Column('chunk_index', sa.Integer(), primary_key=True),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('embedding', vector_type, nullable=False),
        sa.Column('embedded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'profile_id'],
                                ['embedding_profiles.tenant_id', 'embedding_profiles.profile_id'],
                                name='fk_embedding_profile', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id', 'document_id', 'document_version', 'chunk_index',
                                 'content_sha256'],
                                ['knowledge_chunk_versions.tenant_id',
                                 'knowledge_chunk_versions.document_id',
                                 'knowledge_chunk_versions.document_version',
                                 'knowledge_chunk_versions.chunk_index',
                                 'knowledge_chunk_versions.content_sha256'],
                                name='fk_embedding_chunk_content', ondelete='CASCADE'))
    sa.Index('ix_knowledge_embeddings_1024_hnsw', table.c.embedding,
             postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'},
             postgresql_with={'m': 16, 'ef_construction': 64})
    sa.Index('ix_knowledge_embeddings_1024_scope', table.c.tenant_id,
             table.c.profile_id, table.c.document_id, table.c.document_version)
    return table
