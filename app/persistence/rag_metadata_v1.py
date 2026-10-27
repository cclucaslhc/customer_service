"""Frozen local Embedding profiles and staged index manifests.

No vector data is stored in these tables. A later migration must bind actual
chunk-version vectors to a profile before an index may be activated.
"""

import sqlalchemy as sa


def add_rag_metadata(meta):
    profiles = sa.Table('embedding_profiles', meta,
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('identity_tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('profile_id', sa.Uuid(), primary_key=True),
        sa.Column('model_id', sa.Text(), nullable=False),
        sa.Column('model_revision', sa.Text(), nullable=False),
        sa.Column('weights_sha256', sa.Text(), nullable=False),
        sa.Column('license_id', sa.Text(), nullable=False),
        sa.Column('dimension', sa.Integer(), nullable=False),
        sa.Column('distance', sa.Text(), nullable=False),
        sa.Column('normalized', sa.Boolean(), nullable=False),
        sa.Column('max_tokens', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(model_id) BETWEEN 1 AND 300 AND length(model_revision) BETWEEN 1 AND 200 "
                           "AND length(weights_sha256) = 64 AND length(license_id) BETWEEN 1 AND 100 "
                           "AND dimension BETWEEN 1 AND 4096 AND distance IN ('cosine','inner_product','l2') "
                           "AND max_tokens BETWEEN 1 AND 32768", name='embedding_profile_shape'))
    indexes = sa.Table('knowledge_index_versions', meta,
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('identity_tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('index_id', sa.Uuid(), primary_key=True),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('manifest_sha256', sa.Text(), nullable=True),
        sa.Column('lexical_version', sa.Text(), nullable=False),
        sa.Column('chunker_version', sa.Text(), nullable=False),
        sa.Column('reranker_version', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id', 'profile_id'],
                                ['embedding_profiles.tenant_id', 'embedding_profiles.profile_id'],
                                name='fk_knowledge_index_profile', ondelete='RESTRICT'),
        sa.CheckConstraint("status IN ('building','ready','active','retired') AND "
                           "(manifest_sha256 IS NULL OR length(manifest_sha256) = 64) AND "
                           "((status = 'building' AND manifest_sha256 IS NULL AND activated_at IS NULL) "
                           "OR (status = 'ready' AND manifest_sha256 IS NOT NULL AND activated_at IS NULL) "
                           "OR (status = 'active' AND manifest_sha256 IS NOT NULL AND activated_at IS NOT NULL) "
                           "OR (status = 'retired' AND manifest_sha256 IS NOT NULL))",
                           name='knowledge_index_shape'))
    sa.Index('uq_knowledge_index_active_tenant', indexes.c.tenant_id, unique=True,
             postgresql_where=sa.text("status = 'active'"),
             sqlite_where=sa.text("status = 'active'"))
    return profiles, indexes
