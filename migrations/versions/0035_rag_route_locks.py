"""Lock route identities through the caller's short commit transaction."""
from alembic import op
from app.persistence.rag_route_lock_v1 import ROUTE_LOCK_FUNCTION_SQL

revision='0035_rag_route_locks'
down_revision='0034_run_rag_route'
branch_labels=None
depends_on=None


def upgrade():
    op.execute(ROUTE_LOCK_FUNCTION_SQL)
    op.execute('REVOKE ALL ON FUNCTION public.cs_rag_route_lock(uuid,text) FROM PUBLIC')


def downgrade():
    raise RuntimeError('RAG route identity-lock downgrade is disabled')
