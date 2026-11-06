"""Append-only local inference evidence, not provider invoices or CPU pricing.

Uses the existing protected audit ledger and backup/restore path. It never puts
document text, query strings, vectors, scores, credentials or model paths in it.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import re
import uuid
import hashlib

import sqlalchemy as sa

from app.persistence.schema import metadata
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.recovery import require_operational


PURPOSES=frozenset(('embedding_query','embedding_document','rerank'))


class LocalInferenceAccountingError(RuntimeError):
    """A result cannot be published as accounted; never a model-only fallback."""


def _account(operation,*args):
    from app.identity import AuthenticationError,AuthorizationError
    from app.knowledge_retrieval import KnowledgeIndexChanged
    try:
        return operation(*args)
    except (AuthenticationError,AuthorizationError,KnowledgeIndexChanged,sa.exc.SQLAlchemyError):
        raise
    except Exception as exc:
        raise LocalInferenceAccountingError('Local inference accounting failure') from exc


@dataclass
class LocalInferenceObservation:
    input_tokens: int | None = None

    def observe_tokens(self,value):
        if type(value) is not int or not 0 < value <= 16*8192:
            raise ValueError('Invalid local tokenizer observation')
        self.input_tokens=value


class AuditInferenceMeter:
    """One begin receipt before compute, deterministic append-only terminal ID.

    Lost authorization or failed terminal commit leaves the started receipt
    unresolved. It is not silently discarded or turned into a zero-cost success.
    """
    def __init__(self,engine,authorize,*,model_id,weights_sha256):
        if engine.dialect.name!='postgresql' or not callable(authorize):
            raise ValueError('Local inference accounting requires PostgreSQL authority')
        if not isinstance(model_id,str) or not 1<=len(model_id)<=200 or not re.fullmatch('[0-9a-f]{64}',weights_sha256):
            raise ValueError('Pinned local inference model required')
        self.engine,self.authorize=engine,authorize
        self.model_id,self.weights_sha256=model_id,weights_sha256

    @classmethod
    def for_knowledge(cls,repository,weight):
        from app.persistence.knowledge_repository import KnowledgeRepository
        if not isinstance(repository,KnowledgeRepository) or repository._token is None:
            raise ValueError('Authenticated knowledge repository required')
        def authorize(db):
            p=repository._principal(db)
            repository._route_guard(db,p)
            return {'tenant_id':uuid.UUID(p.tenant_id),'actor_id':uuid.UUID(p.user_id),
                    'actor':p.user_id,'target_tenant_id':p.tenant_id}
        return cls(repository.engine,authorize,model_id=weight.repository,weights_sha256=weight.sha256)

    @classmethod
    def for_index_worker(cls,worker,weight):
        def authorize(db):
            worker._check_profile(db)
            # Like RagIndexAdmin: database operators are not tenant members.
            # Preserve explicit control attribution; never invent a tenant user.
            actor=db.execute(sa.text('SELECT current_user')).scalar_one()
            return {'tenant_id':uuid.UUID(LEGACY_TENANT_ID),'actor_id':None,
                    'actor':actor,'target_tenant_id':str(worker.tenant_id)}
        return cls(worker.engine,authorize,model_id=weight.repository,weights_sha256=weight.sha256)

    def begin(self,purpose,items,max_input_tokens):
        if purpose not in PURPOSES or type(items) is not int or not 1<=items<=16:
            raise ValueError('Invalid local inference operation')
        if type(max_input_tokens) is not int or not items<=max_input_tokens<=16*8192:
            raise ValueError('Invalid local inference token bound')
        attempt=uuid.uuid4()
        with self.engine.begin() as db:
            require_operational(db)
            actor=self.authorize(db)
            details={'schema_version':1,'purpose':purpose,'provider':'local','model_id':self.model_id,
                'weights_sha256':self.weights_sha256,'target_tenant_id':actor['target_tenant_id'],
                'items':items,'max_input_tokens':max_input_tokens,'input_tokens':None,
                'api_billing':'not_applicable_offline_v1','infrastructure_amount':None}
            db.execute(metadata.tables['audit_logs'].insert().values(id=attempt,
                tenant_id=actor['tenant_id'],actor_id=actor['actor_id'],actor=actor['actor'],
                action='local_inference.started',entity_type='inference_attempt',entity_id=str(attempt),
                summary='Local inference started; usage and infrastructure cost unresolved',
                metadata_json=details,created_at=sa.func.clock_timestamp()))
        return str(attempt)

    def finish(self,attempt,state,input_tokens):
        attempt=uuid.UUID(str(attempt))
        if state not in ('completed','error','cancelled','incomplete'):
            raise ValueError('Invalid local inference terminal state')
        if input_tokens is not None and (type(input_tokens) is not int or not 0<input_tokens<=16*8192):
            raise ValueError('Invalid local tokenizer count')
        terminal=uuid.uuid5(attempt,'local-inference-terminal-v1')
        table=metadata.tables['audit_logs']
        with self.engine.begin() as db:
            require_operational(db)
            actor=self.authorize(db)
            # Audit rows are SELECT/INSERT only for API/Worker roles. Serialize
            # terminal receipts without requiring UPDATE privilege on evidence.
            lock=int.from_bytes(hashlib.sha256(('local-inference:'+str(attempt)).encode()).digest()[:8],'big')&((1<<63)-1)
            db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
            started=db.execute(sa.select(table).where(table.c.id==attempt,
                table.c.tenant_id==actor['tenant_id'],table.c.action=='local_inference.started')).mappings().one()
            if started['actor_id']!=actor['actor_id'] or started['actor']!=actor['actor']:
                raise PermissionError('Local inference authority changed')
            details=started['metadata_json']
            if (details['target_tenant_id']!=actor['target_tenant_id'] or details['model_id']!=self.model_id
                    or details['weights_sha256']!=self.weights_sha256):
                raise PermissionError('Local inference scope changed')
            if input_tokens is not None and input_tokens>details['max_input_tokens']:
                raise ValueError('Observed local tokens exceed declared bound')
            result={**details,'state':state,'input_tokens':input_tokens,
                'usage_status':'tokenizer_observed' if input_tokens is not None else 'unknown'}
            existing=db.execute(sa.select(table.c.metadata_json).where(table.c.id==terminal,
                table.c.tenant_id==actor['tenant_id'])).scalar_one_or_none()
            if existing is not None:
                if existing!=result: raise ValueError('Local inference terminal conflict')
                return
            db.execute(table.insert().values(id=terminal,tenant_id=actor['tenant_id'],
                actor_id=actor['actor_id'],actor=actor['actor'],action='local_inference.finished',
                entity_type='inference_attempt',entity_id=str(attempt),
                summary='Local inference ended; infrastructure cost remains unresolved',
                metadata_json=result,created_at=sa.func.clock_timestamp()))

    @contextmanager
    def measure(self,purpose,items,max_input_tokens):
        attempt=_account(self.begin,purpose,items,max_input_tokens)
        observation=LocalInferenceObservation()
        try:
            yield observation
        except BaseException as exc:
            import asyncio
            state='cancelled' if isinstance(exc,asyncio.CancelledError) else 'error' if isinstance(exc,Exception) else 'incomplete'
            try: self.finish(attempt,state,observation.input_tokens)
            except Exception: pass # Keep the started receipt; no privileged cleanup.
            raise
        else:
            # Accounting failure prevents publishing a supposedly accounted result.
            _account(self.finish,attempt,'completed',observation.input_tokens)
