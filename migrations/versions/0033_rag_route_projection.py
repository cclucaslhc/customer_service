"""Restricted, session-scoped projection; not runtime gray rollout activation."""
from alembic import op
from app.persistence.rag_route_v1 import ROUTE_FUNCTION_SQL

revision='0033_rag_route_projection'
down_revision='0032_rag_rollouts'
branch_labels=None
depends_on=None


def upgrade():
    op.execute(ROUTE_FUNCTION_SQL)
    op.execute('REVOKE ALL ON FUNCTION public.cs_rag_route(uuid,text) FROM PUBLIC')


def downgrade():
    raise RuntimeError('RAG route projection downgrade is disabled')
