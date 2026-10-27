"""Internal route snapshots; caller must authorize the chat session first."""
import uuid
import hashlib
import json
import sqlalchemy as sa
from app.identity import AuthenticationError, token_hash
from app.persistence.base import wire


def binding_for_runtime(provider,model,instructions):
    """V1 binds exact model identity and complete effective instruction bytes.

    No keys enter this digest. Endpoint/settings support must extend this
    versioned contract before those become request-configurable.
    """
    encoded=json.dumps({'schema_version':1,'provider':provider,'model':model},
        sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return {'model_config_sha256':hashlib.sha256(encoded).hexdigest(),
        'prompt_sha256':hashlib.sha256(instructions.encode('utf-8')).hexdigest()}


def context_from_route(route):
    return ({key:route[key] for key in ('model_config_sha256','prompt_sha256')}
        if 'model_config_sha256' in route and 'prompt_sha256' in route else None)


def current_route(db,*,credential,tenant_id,session_id,lock_identities=False,runtime_context=None):
    tenant,session=uuid.UUID(str(tenant_id)),uuid.UUID(str(session_id))
    projection=None
    if credential is not None:
        function='cs_rag_route_lock' if lock_identities else 'cs_rag_route'
        try:
            projection=db.execute(sa.text(f'SELECT * FROM public.{function}(:session,:hash)'),
                {'session':session,'hash':token_hash(credential)}).mappings().first()
        except sa.exc.DBAPIError as exc:
            if getattr(exc.orig,'sqlstate',None)=='28000':
                raise AuthenticationError() from None
            raise
    if projection:
        result=wire(projection)
        if result['tenant_id']!=str(tenant):
            raise ValueError('Route tenant mismatch')
        if runtime_context is not None:
            matches=db.execute(sa.text('SELECT public.cs_rag_route_context(:session,:hash,:model,:prompt)'),
                {'session':session,'hash':token_hash(credential),'model':runtime_context['model_config_sha256'],
                 'prompt':runtime_context['prompt_sha256']}).scalar_one()
            if not matches:
                from app.knowledge_retrieval import KnowledgeIndexChanged
                raise KnowledgeIndexChanged()
    else:
        index=db.execute(sa.text("SELECT index_id FROM public.knowledge_index_versions "
            "WHERE tenant_id=:tenant AND status='active'"),{'tenant':tenant}).scalar_one_or_none()
        result={'tenant_id':str(tenant),'rollout_id':None,'release_id':None,
            'route_version':None,'index_id':str(index) if index else None}
    return {**result,'session_id':str(session),'schema_version':1,**(runtime_context or {})}
