"""Owner-controlled frozen release intent; ordinary services have no access."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_rag_release_intents(meta):
    return sa.Table('rag_release_intents',meta,
        sa.Column('tenant_id',sa.Uuid(),primary_key=True),
        sa.Column('release_id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_index_id',sa.Uuid(),nullable=False),
        sa.Column('previous_index_id',sa.Uuid(),nullable=False),
        sa.Column('reviewer_a',sa.Uuid(),nullable=False),
        sa.Column('reviewer_b',sa.Uuid(),nullable=False),
        sa.Column('body',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.Column('body_sha256',sa.Text(),nullable=False),
        sa.Column('state',sa.Text(),nullable=False,server_default='pending'),
        sa.Column('approval_a_session_hash',sa.Text()),
        sa.Column('approval_b_session_hash',sa.Text()),
        sa.Column('approval_a_at',sa.DateTime(timezone=True)),
        sa.Column('approval_b_at',sa.DateTime(timezone=True)),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['tenant_id','candidate_index_id'],
            ['knowledge_index_versions.tenant_id','knowledge_index_versions.index_id'],
            name='fk_rag_release_candidate_index',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','previous_index_id'],
            ['knowledge_index_versions.tenant_id','knowledge_index_versions.index_id'],
            name='fk_rag_release_previous_index',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','reviewer_a'],
            ['identity_memberships.tenant_id','identity_memberships.user_id'],
            name='fk_rag_release_reviewer_a',ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['tenant_id','reviewer_b'],
            ['identity_memberships.tenant_id','identity_memberships.user_id'],
            name='fk_rag_release_reviewer_b',ondelete='RESTRICT'),
        sa.CheckConstraint("candidate_index_id <> previous_index_id AND reviewer_a <> reviewer_b "
            "AND length(body_sha256)=64 AND length(CAST(body AS TEXT)) BETWEEN 2 AND 65536 "
            "AND version>=0 AND expires_at>created_at "
            "AND state IN ('pending','approved','executed','rejected','recovery_review') "
            "AND ((approval_a_session_hash IS NULL AND approval_a_at IS NULL) OR "
            "(approval_a_session_hash IS NOT NULL AND length(approval_a_session_hash)=64 AND approval_a_at IS NOT NULL)) "
            "AND ((approval_b_session_hash IS NULL AND approval_b_at IS NULL) OR "
            "(approval_b_session_hash IS NOT NULL AND length(approval_b_session_hash)=64 AND approval_b_at IS NOT NULL)) "
            "AND (approval_a_session_hash IS NULL OR approval_b_session_hash IS NULL "
            "OR approval_a_session_hash <> approval_b_session_hash) "
            "AND (state NOT IN ('approved','executed') OR "
            "(approval_a_session_hash IS NOT NULL AND approval_b_session_hash IS NOT NULL))",
            name='rag_release_intent_shape'))
