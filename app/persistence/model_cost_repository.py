"""Exact decimal estimates and append-only, explicitly asserted invoice statements."""
from decimal import Decimal, ROUND_UP, localcontext
import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone
import sqlalchemy as sa

from app.persistence.schema import metadata
from app.persistence.model_cost_v1 import RATES
from app.persistence.identity_repository import IdentityRepository
from app.persistence.maintenance_current import WRITE_GATE_KEY
from app.recovery import require_operational
from app.identity import AuthorizationError
from app.persistence.base import wire


class CostConflict(RuntimeError):
    pass


def money(value, *, maximum=Decimal('1000000')):
    if type(value) is str:
        if not re.fullmatch(r'[0-9]{1,19}(\.[0-9]{1,9})?',value):
            raise ValueError('Invalid decimal amount')
        value=Decimal(value)
    if not isinstance(value,Decimal) or not value.is_finite() or not 0 <= value <= maximum:
        raise ValueError('Invalid decimal amount')
    with localcontext() as ctx:
        ctx.prec=40
        if value.quantize(Decimal('.000000001')) != value:
            raise ValueError('Invalid decimal precision')
    return value


def digest(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('Invalid evidence digest')
    return value


def price_lock(db,tenant,provider,model):
    key=int.from_bytes(hashlib.sha256(f'cost-price:{tenant}:{provider}:{model}'.encode()).digest()[:8],'big') & ((1<<63)-1)
    db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})


def pin_quote(db, attempt):
    p,q=metadata.tables['model_price_versions'],metadata.tables['model_price_quotes']
    price_lock(db,attempt['tenant_id'],attempt['provider'],attempt['model'])
    price=db.execute(sa.select(p.c.price_id).where(p.c.tenant_id==attempt['tenant_id'],
        p.c.provider==attempt['provider'],p.c.model==attempt['model']).order_by(p.c.version.desc()).limit(1)).scalar_one_or_none()
    db.execute(q.insert().values(tenant_id=attempt['tenant_id'],attempt_id=attempt['attempt_id'],
        price_id=price,quoted_at=sa.func.clock_timestamp()))


def estimate(attempt, price, *, quote_exists=True):
    currency=price['currency'] if price else None
    unknown={'status':'unknown','amount':None,'currency':currency}
    if not quote_exists:
        return {**unknown,'reason':'no_quote'}
    if price is None:
        return {**unknown,'reason':'no_price'}
    if attempt['usage_status']=='unknown':
        return {**unknown,'reason':'unknown_usage'}
    if attempt['usage_status']!='observed' or attempt['state']!='completed':
        return {**unknown,'reason':'partial_usage'}
    regular=attempt['input_tokens']-attempt['cache_read_tokens']-attempt['cache_write_tokens']
    if regular < 0:
        return {**unknown,'reason':'invalid_cache'}
    with localcontext() as ctx:
        ctx.prec=50
        amount=(Decimal(regular)*price['input_rate']+Decimal(attempt['output_tokens'])*price['output_rate']+
            Decimal(attempt['cache_read_tokens'])*price['cache_read_rate']+
            Decimal(attempt['cache_write_tokens'])*price['cache_write_rate'])/Decimal(1000000)
        amount=amount.quantize(Decimal('.000000001'),rounding=ROUND_UP)
    return {'status':'estimated','reason':'sdk_observed','currency':currency,'amount':amount}


def append_estimate(db, attempt):
    p,q,c=(metadata.tables[n] for n in ('model_price_versions','model_price_quotes','model_cost_assessments'))
    quote=db.execute(sa.select(q).where(q.c.attempt_id==attempt['attempt_id'],q.c.tenant_id==attempt['tenant_id'])).mappings().first()
    price=db.execute(sa.select(p).where(p.c.price_id==quote['price_id'],p.c.tenant_id==attempt['tenant_id'])).mappings().first() if quote and quote['price_id'] else None
    value=estimate(attempt,price,quote_exists=quote is not None)
    db.execute(c.insert().values(assessment_id=uuid.uuid4(),tenant_id=attempt['tenant_id'],
        attempt_id=attempt['attempt_id'],revision=0,kind='estimate',created_at=sa.func.clock_timestamp(),**value))


class ModelCostRepository:
    def __init__(self,engine,*,token):
        if engine.dialect.name!='postgresql' or token is None:
            raise ValueError('Costs require authenticated PostgreSQL')
        self.engine,self.token=engine,token

    def _principal(self,db,*,writing=False):
        db.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        db.execute(sa.text("SET LOCAL lock_timeout='1s'"))
        if writing:
            db.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':WRITE_GATE_KEY})
        require_operational(db)
        p=IdentityRepository(self.engine).resolve(self.token,connection=db)
        p.require('config.manage')
        return p

    def _audit(self,db,p,action,entity_id,revision):
        db.execute(metadata.tables['audit_logs'].insert().values(id=uuid.uuid4(),tenant_id=uuid.UUID(p.tenant_id),
            actor_id=uuid.UUID(p.user_id),actor=p.user_id,action=action,entity_type='model_cost',entity_id=str(entity_id),
            summary='Explicit model cost administration',metadata_json={'revision':revision},created_at=sa.func.clock_timestamp()))

    def current_price(self, *, provider, model):
        if provider not in {'deepseek','openai','local','synthetic','unknown'} or not isinstance(model,str) or not 1<=len(model)<=200:
            raise ValueError('Invalid price identity')
        table=metadata.tables['model_price_versions']
        with self.engine.begin() as db:
            actor=self._principal(db); tenant=uuid.UUID(actor.tenant_id)
            row=db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.provider==provider,table.c.model==model).order_by(table.c.version.desc()).limit(1)).mappings().first()
            if row is None:
                return None
            result={name:row[name] for name in ('provider','model','version','currency',*RATES,'source_sha256','published_at')}
            for name in RATES:
                result[name]=format(result[name],'.9f')
            return wire(result)

    def publish_price(self,*,publication_id,expected_version,provider,model,currency,source_sha256,**rates):
        if (provider not in {'deepseek','openai','local','synthetic','unknown'} or not isinstance(model,str)
            or not 1<=len(model)<=200 or currency not in {'CNY','USD'} or type(expected_version) is not int
            or expected_version<0 or set(rates)!=set(RATES)):
            raise ValueError('Invalid price specification')
        publication_id=uuid.UUID(str(publication_id))
        values={'provider':provider,'model':model,'currency':currency,'source_sha256':digest(source_sha256),
                **{name:money(value) for name,value in rates.items()}}
        table=metadata.tables['model_price_versions']
        with self.engine.begin() as db:
            p=self._principal(db,writing=True); tenant=uuid.UUID(p.tenant_id)
            price_lock(db,tenant,provider,model)
            old=db.execute(sa.select(table).where(table.c.tenant_id==tenant,table.c.publication_id==publication_id)).mappings().first()
            if old:
                if old['version']!=expected_version+1 or any(old[k]!=v for k,v in values.items()):
                    raise CostConflict('Price publication identifier conflict')
                return wire(old)
            current=db.execute(sa.select(sa.func.coalesce(sa.func.max(table.c.version),0)).where(
                table.c.tenant_id==tenant,table.c.provider==provider,table.c.model==model)).scalar_one()
            if current!=expected_version:
                raise CostConflict('Price version changed')
            price_id=uuid.uuid4()
            row=db.execute(table.insert().values(price_id=price_id,tenant_id=tenant,publication_id=publication_id,
                actor_id=uuid.UUID(p.user_id),version=current+1,published_at=sa.func.clock_timestamp(),**values).returning(table)).mappings().one()
            self._audit(db,p,'cost.price.publish',price_id,current+1)
            return wire(row)

    def reconcile(self,*,attempt_id,request_id,expected_revision,amount,currency,receipt_sha256):
        if type(expected_revision) is not int or expected_revision<0 or currency not in {'CNY','USD'}:
            raise ValueError('Invalid reconciliation specification')
        attempt_id,request_id=uuid.UUID(str(attempt_id)),uuid.UUID(str(request_id))
        amount=money(amount,maximum=Decimal('1000000000000000000')); receipt=digest(receipt_sha256)
        a,c,q,p=(metadata.tables[n] for n in ('model_usage_attempts','model_cost_assessments','model_price_quotes','model_price_versions'))
        with self.engine.begin() as db:
            actor=self._principal(db,writing=True); tenant=uuid.UUID(actor.tenant_id)
            from app.persistence.model_budget_repository import budget_lock
            budget_lock(db,tenant)
            row=db.execute(sa.select(a).where(a.c.tenant_id==tenant,a.c.attempt_id==attempt_id).with_for_update()).mappings().first()
            if row is None:
                raise AuthorizationError('Cost attempt unavailable')
            old=db.execute(sa.select(c).where(c.c.tenant_id==tenant,c.c.request_id==request_id)).mappings().first()
            if old:
                if old['revision']!=expected_revision+1 or old['attempt_id']!=attempt_id or old['amount']!=amount or old['currency']!=currency or old['receipt_sha256']!=receipt:
                    raise CostConflict('Cost reconciliation identifier conflict')
                return wire(old)
            version=db.execute(sa.select(sa.func.coalesce(sa.func.max(c.c.revision),0)).where(c.c.tenant_id==tenant,c.c.attempt_id==attempt_id)).scalar_one()
            if version!=expected_revision:
                raise CostConflict('Cost reconciliation changed')
            previous_currency=db.execute(sa.select(c.c.currency).where(c.c.tenant_id==tenant,c.c.attempt_id==attempt_id,
                c.c.kind=='reconcile').order_by(c.c.revision.desc()).limit(1)).scalar_one_or_none()
            quoted_currency=db.execute(sa.select(p.c.currency).join(q,sa.and_(q.c.price_id==p.c.price_id,q.c.tenant_id==p.c.tenant_id))
                .where(q.c.attempt_id==attempt_id,q.c.tenant_id==tenant)).scalar_one_or_none()
            if (previous_currency or quoted_currency) not in (None,currency):
                raise CostConflict('Currency conversion is not implicit')
            if db.execute(sa.select(c.c.assessment_id).where(c.c.tenant_id==tenant,c.c.receipt_sha256==receipt)).first():
                raise CostConflict('Receipt already associated with a cost statement')
            result=db.execute(c.insert().values(assessment_id=uuid.uuid4(),tenant_id=tenant,attempt_id=attempt_id,
                revision=version+1,kind='reconcile',status='operator_reported',reason='operator_statement',
                amount=amount,currency=currency,receipt_sha256=receipt,request_id=request_id,
                actor_id=uuid.UUID(actor.user_id),created_at=sa.func.clock_timestamp()).returning(c)).mappings().one()
            self._audit(db,actor,'cost.reconcile.append',attempt_id,version+1)
            return wire(result)

    def history(self,attempt_id):
        attempt=uuid.UUID(str(attempt_id)); table=metadata.tables['model_cost_assessments']
        with self.engine.begin() as db:
            p=self._principal(db); tenant=uuid.UUID(p.tenant_id)
            if not db.execute(sa.select(metadata.tables['model_usage_attempts'].c.attempt_id).where(
                metadata.tables['model_usage_attempts'].c.tenant_id==tenant,
                metadata.tables['model_usage_attempts'].c.attempt_id==attempt)).first():
                raise AuthorizationError('Cost attempt unavailable')
            return [wire(row) for row in db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.attempt_id==attempt).order_by(table.c.revision)).mappings()]

    def exposure(self, *, run_id=None, utc_day=None):
        """Conservative read snapshot, NOT a reservation or authorization to spend.

        Downward operator corrections never release exposure. Unknown SDK usage
        remains unresolved even with an operator statement. One SQL statement
        gives a consistent snapshot; callers must not use it for check-then-call.
        """
        if utc_day is not None and (type(utc_day) is not date or utc_day==date.max):
            raise ValueError('UTC day must be a date')
        run = uuid.UUID(str(run_id)) if run_id is not None else None
        a,c,q,p=(metadata.tables[n] for n in ('model_usage_attempts','model_cost_assessments',
                                            'model_price_quotes','model_price_versions'))
        with self.engine.begin() as db:
            actor=self._principal(db); tenant=uuid.UUID(actor.tenant_id)
            if run is not None:
                runs=metadata.tables['agent_runs']
                if not db.execute(sa.select(runs.c.id).where(runs.c.id==run,runs.c.tenant_id==tenant)).first():
                    raise AuthorizationError('Cost Run unavailable')
            # Aggregate per attempt first: revisions are alternatives, not charges.
            statements=sa.select(c.c.attempt_id,
                sa.case((sa.func.count(sa.distinct(c.c.currency))<=1,
                         sa.func.max(c.c.amount)),else_=None).label('peak'),
                sa.func.min(c.c.currency).label('currency'),
                sa.func.count(sa.distinct(c.c.currency)).label('currencies'),
                sa.func.bool_or(sa.and_(c.c.kind=='estimate',c.c.status=='estimated')).label('observed')
            ).where(c.c.tenant_id==tenant).group_by(c.c.attempt_id).subquery()
            currency=sa.func.coalesce(statements.c.currency,p.c.currency)
            scoped=sa.select(currency.label('currency'),statements.c.peak,
                sa.func.coalesce(statements.c.observed,False).label('observed'),
                sa.func.coalesce(statements.c.currencies,0).label('currencies')
            ).select_from(a.outerjoin(statements,a.c.attempt_id==statements.c.attempt_id)
                .outerjoin(q,sa.and_(q.c.attempt_id==a.c.attempt_id,q.c.tenant_id==a.c.tenant_id))
                .outerjoin(p,sa.and_(p.c.price_id==q.c.price_id,p.c.tenant_id==a.c.tenant_id))
            ).where(a.c.tenant_id==tenant)
            if run is not None:
                scoped=scoped.where(a.c.run_id==run)
            if utc_day is not None:
                start=datetime.combine(utc_day,datetime.min.time(),tzinfo=timezone.utc)
                scoped=scoped.where(a.c.started_at>=start,a.c.started_at<start+timedelta(days=1))
            scoped=scoped.subquery()
            rows=db.execute(sa.select(scoped.c.currency,
                sa.func.count().label('attempts'),
                sa.func.count().filter(sa.or_(~scoped.c.observed,scoped.c.currencies>1)).label('unresolved'),
                sa.func.coalesce(sa.func.sum(scoped.c.peak),0).label('retained_amount')
            ).group_by(scoped.c.currency).order_by(scoped.c.currency)).mappings()
            return [dict(row,retained_amount=format(row['retained_amount'],'.9f'),
                         amount=None if row['unresolved'] else format(row['retained_amount'],'.9f')) for row in rows]
