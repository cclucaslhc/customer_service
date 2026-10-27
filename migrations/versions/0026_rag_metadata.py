"""Staged RAG profile and index manifests; no active vector route yet."""

from alembic import op
import sqlalchemy as sa

from app.persistence.schema_v1 import metadata as baseline
from app.persistence.rag_metadata_v1 import add_rag_metadata

revision = '0026_rag_metadata'
down_revision = '0025_model_circuit'
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData(naming_convention=baseline.naming_convention)
    sa.Table('identity_tenants', meta, sa.Column('id', sa.Uuid(), primary_key=True))
    profiles, indexes = add_rag_metadata(meta)
    profiles.create(op.get_bind(), checkfirst=False)
    indexes.create(op.get_bind(), checkfirst=False)
    for name in ('embedding_profiles', 'knowledge_index_versions'):
        op.execute(f'CREATE TRIGGER cs_maintenance_write BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
                   f'ON public.{name} FOR EACH STATEMENT '
                   'EXECUTE FUNCTION public.cs_maintenance_write_guard()')


def downgrade():
    raise RuntimeError('RAG profile/index downgrade is disabled: preserve version evidence')
