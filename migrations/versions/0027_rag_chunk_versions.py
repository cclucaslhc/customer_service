"""Versioned chunk snapshots; existing documents are backfilled separately."""

from alembic import op
import sqlalchemy as sa

from app.persistence.schema_v1 import metadata as baseline
from app.persistence.rag_chunk_v1 import add_rag_chunk_versions

revision = '0027_rag_chunk_versions'
down_revision = '0026_rag_metadata'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('documents', meta, sa.Column('tenant_id', sa.Uuid()), sa.Column('id', sa.Uuid()))
    table = add_rag_chunk_versions(meta)
    table.create(op.get_bind(), checkfirst=False)
    op.execute('CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
               'ON public.knowledge_chunk_versions FOR EACH STATEMENT '
               'EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('Chunk-version downgrade is disabled: preserve index source evidence')
