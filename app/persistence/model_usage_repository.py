"""Run-scoped short transactions; billing evidence independent of Trace availability."""
import uuid
import sqlalchemy as sa
from app.run_leases import RunLeaseLost
from app.persistence.base import wire

TOKEN_FIELDS = ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')


class ModelFallbackDenied(RuntimeError):
    """No tenant authority for this different model at attempt start."""


def usage_observation(usage, state):
    # SDK zero defaults cannot establish whether the supplier reported zero.
    values = {name: getattr(usage, name, None) for name in TOKEN_FIELDS}
    if (any(type(v) is not int or not 0 <= v <= 9223372036854775807 for v in values.values())
        or values['cache_read_tokens'] > values['input_tokens']
        or values['cache_write_tokens'] > values['input_tokens']
        or not (values['input_tokens'] or values['output_tokens'])):
        return {'usage_status':'unknown', **dict.fromkeys(TOKEN_FIELDS)}
    return {'usage_status':'observed' if state == 'completed' else 'partial', **values}


class ModelUsageRepository:
    def __init__(self, chat, run_id, *, provider, model):
        if chat.engine.dialect.name != 'postgresql' or chat._lease is None:
            raise ValueError('Model usage requires a fenced PostgreSQL Run')
        if provider not in {'deepseek','openai','local','synthetic','unknown'} or not isinstance(model,str) or not 1 <= len(model) <= 200:
            raise ValueError('Invalid model usage configuration')
        self.chat, self.run_id = chat, uuid.UUID(str(run_id))
        self.provider, self.model = provider, model
        self.table = chat.tables['model_usage_attempts']

    @property
    def circuit(self):
        from app.model_circuit import ModelCircuit
        return ModelCircuit(self)

    def _run(self, db, *, finishing=False):
        db.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        db.execute(sa.text("SET LOCAL lock_timeout='1s'"))
        runs = self.chat.tables['agent_runs']
        row = db.execute(sa.select(runs).where(runs.c.id == self.run_id)).mappings().first()
        if row is None:
            raise RunLeaseLost()
        self.chat._lock_session(db, row['session_id'])
        row = db.execute(sa.select(runs).where(runs.c.id == self.run_id).with_for_update()).mappings().one()
        if (not self.chat._allowed(db,row['session_id']) or row['status'] != 'running'
            or not self.chat._lease_current(db,row,allow_cancel=finishing)):
            raise RunLeaseLost()
        principal = self.chat._principal(db)
        if not finishing and (row['provider'], row['model']) != (self.provider, self.model):
            from app.persistence.model_fallback_repository import allows_fallback_attempt
            if not allows_fallback_attempt(db, tenant_id=row['tenant_id'],
                    primary_provider=row['provider'], provider=self.provider, model=self.model):
                raise ModelFallbackDenied('Model fallback is not authorized')
        return row, principal

    def begin(self, attempt_id, *, request=None):
        attempt = uuid.UUID(str(attempt_id))
        with self.chat.engine.begin() as db:
            row, principal = self._run(db)
            from app.persistence.model_budget_repository import budget_lock, reserve
            budget_lock(db,row['tenant_id'])
            # Duplicate attempts fail, never authorize a repeat charge.
            inserted=db.execute(self.table.insert().values(attempt_id=attempt,tenant_id=row['tenant_id'],
                run_id=self.run_id,actor_id=principal.user_id if principal else None,
                lease_owner=row['lease_owner'],lease_fence=row['lease_fence'],
                provider=self.provider,model=self.model,purpose='chat',state='started',
                usage_status='unknown',started_at=sa.func.clock_timestamp()).returning(self.table)).mappings().one()
            from app.persistence.model_cost_repository import pin_quote
            pin_quote(db,inserted)
            return reserve(db,inserted,request)

    def finish(self, attempt_id, state, usage=None):
        if state not in {'completed','error','cancelled','incomplete'}:
            raise ValueError('Invalid model usage terminal state')
        values = usage_observation(usage,state)
        with self.chat.engine.begin() as db:
            run, _ = self._run(db,finishing=True)
            from app.persistence.model_budget_repository import budget_lock
            budget_lock(db,run['tenant_id'])
            row = db.execute(sa.select(self.table).where(self.table.c.attempt_id==uuid.UUID(str(attempt_id)),
                self.table.c.run_id==self.run_id,self.table.c.tenant_id==run['tenant_id']).with_for_update()).mappings().first()
            if (row is None or row['lease_owner'] != run['lease_owner'] or row['lease_fence'] != run['lease_fence']
                or row['provider'] != self.provider or row['model'] != self.model):
                raise RunLeaseLost()
            if row['state'] != 'started':
                if row['state'] != state or any(row[k] != v for k,v in values.items()):
                    raise ValueError('Model usage finalization conflict')
                return
            db.execute(self.table.update().where(self.table.c.attempt_id==row['attempt_id']).values(
                state=state,finished_at=sa.func.clock_timestamp(),**values))
            from app.persistence.model_cost_repository import append_estimate
            append_estimate(db,{**row,'state':state,**values})

    def list_attempts(self):
        # Read authorization must still work after a Run's lease/terminal transition.
        runs = self.chat.tables['agent_runs']
        with self.chat.engine.connect() as db:
            run = db.execute(sa.select(runs).where(runs.c.id==self.run_id)).mappings().first()
            if not run or not self.chat._allowed(db,run['session_id']):
                raise RunLeaseLost()
            return [wire(row) for row in db.execute(sa.select(self.table).where(
                self.table.c.run_id==self.run_id,self.table.c.tenant_id==run['tenant_id'])
                .order_by(self.table.c.started_at,self.table.c.attempt_id)).mappings()]
