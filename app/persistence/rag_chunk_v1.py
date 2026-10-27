"""Immutable version snapshots for PostgreSQL RAG indexing."""

import sqlalchemy as sa


def add_rag_chunk_versions(meta):
    table = sa.Table('knowledge_chunk_versions', meta,
        sa.Column('tenant_id', sa.Uuid(), primary_key=True),
        sa.Column('document_id', sa.Uuid(), primary_key=True),
        sa.Column('document_version', sa.Integer(), primary_key=True),
        sa.Column('chunk_index', sa.Integer(), primary_key=True),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('section', sa.Text(), nullable=True),
        sa.Column('location', sa.Text(), nullable=True),
        sa.Column('block_type', sa.Text(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('parser_version', sa.Text(), nullable=False),
        sa.Column('chunker_version', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'],
                                name='fk_chunk_version_document', ondelete='CASCADE'),
        sa.CheckConstraint('document_version > 0 AND chunk_index >= 0 AND length(content_sha256) = 64 '
                           'AND length(content) > 0 AND length(parser_version) > 0 '
                           'AND length(chunker_version) > 0', name='knowledge_chunk_version_shape'),
        sa.UniqueConstraint('tenant_id', 'document_id', 'document_version', 'chunk_index',
                            'content_sha256', name='uq_chunk_version_content'))
    sa.Index('ix_chunk_versions_tenant_document_version', table.c.tenant_id,
             table.c.document_id, table.c.document_version)
    return table
