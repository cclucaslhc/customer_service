"""Protected rollout state, not a runtime candidate-read permission."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def add_rag_rollouts(meta):
    table = sa.Table('rag_rollouts', meta,
        sa.Column('tenant_id', sa.Uuid(), primary_key=True),
        sa.Column('rollout_id', sa.Uuid(), primary_key=True),
        sa.Column('release_id', sa.Uuid(), nullable=False),
        sa.Column('plan', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('plan_sha256', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False, server_default='staged'),
        sa.Column('step_index', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('basis_points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'release_id'],
            ['rag_release_intents.tenant_id', 'rag_release_intents.release_id'],
            name='fk_rag_rollout_release', ondelete='RESTRICT'),
        sa.UniqueConstraint('tenant_id', 'release_id', name='uq_rag_rollout_release'),
        sa.CheckConstraint("version >= 0 AND step_index BETWEEN -1 AND 15 "
            "AND basis_points BETWEEN 0 AND 10000 AND expires_at > created_at "
            "AND plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND jsonb_typeof(plan)='object' AND octet_length(plan::text) BETWEEN 2 AND 65536 "
            "AND state IN ('staged','running','paused','completed','rolled_back','recovery_review') "
            "AND (state <> 'staged' OR (step_index=-1 AND basis_points=0)) "
            "AND (state <> 'running' OR (step_index>=0 AND basis_points BETWEEN 1 AND 9999)) "
            "AND (state <> 'completed' OR (step_index>=0 AND basis_points=10000)) "
            "AND (state NOT IN ('rolled_back','recovery_review') OR basis_points=0)",
            name='rag_rollout_shape'))
    sa.Index('uq_rag_rollout_live_tenant', table.c.tenant_id, unique=True,
        postgresql_where=table.c.state.in_(('staged','running','paused','recovery_review')))
    return table
