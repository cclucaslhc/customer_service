"""Narrow approved model/Prompt context predicate; no secret/config table exposure."""
from alembic import op

revision='0036_rag_runtime_binding'
down_revision='0035_rag_route_locks'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE FUNCTION public.cs_rag_route_context(p_session uuid,p_hash text,p_model text,p_prompt text)
      RETURNS boolean LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
      SELECT EXISTS (SELECT 1 FROM public.cs_rag_route(p_session,p_hash) r
        JOIN public.rag_release_intents i ON i.tenant_id=r.tenant_id AND i.release_id=r.release_id
        WHERE i.body->'binding'->>'model_config_sha256'=p_model
          AND i.body->'binding'->>'prompt_sha256'=p_prompt)
      $$""")
    op.execute('REVOKE ALL ON FUNCTION public.cs_rag_route_context(uuid,text,text,text) FROM PUBLIC')


def downgrade():
    raise RuntimeError('Runtime approval binding downgrade is disabled')
