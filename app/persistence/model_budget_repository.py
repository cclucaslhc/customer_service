"""Tenant-serialized conservative budgets; reservations never expire/release silently."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext
import hashlib
import uuid
import sqlalchemy as sa

from app.model_budget import ModelBudgetError, ModelBudgetExceeded, bounded_settings
from app.persistence.schema import metadata
from app.persistence.model_cost_repository import ModelCostRepository, CostConflict, money, digest
from app.persistence.base import wire


def budget_lock(db, tenant):
    key=int.from_bytes(hashlib.sha256(f'model-budget:{tenant}'.encode()).digest()[:8],'big') & ((1<<63)-1)
    db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})


def exposure(db, tenant, currency, *, run_id, started_at, exclude=None):
    """One snapshot with permanent reservations and historical assessment peaks."""
    a,c,r,q,p=(metadata.tables[n] for n in ('model_usage_attempts','model_cost_assessments',
        'model_budget_reservations','model_price_quotes','model_price_versions'))
    history=sa.select(c.c.attempt_id,sa.func.max(c.c.amount).label('peak'),
        sa.func.min(c.c.currency).label('currency'),sa.func.count(sa.distinct(c.c.currency)).label('currencies'),
        sa.func.bool_or(sa.and_(c.c.kind=='estimate',c.c.status=='estimated')).label('observed')
    ).where(c.c.tenant_id==tenant).group_by(c.c.attempt_id).subquery()
    known_currency=sa.func.coalesce(r.c.currency,history.c.currency,p.c.currency)
    uncertain=sa.or_(
        sa.and_(r.c.attempt_id.is_(None),~sa.func.coalesce(history.c.observed,False)),
        sa.func.coalesce(history.c.currencies,0)>1,
        known_currency.is_(None),known_currency!=currency,
        sa.and_(history.c.currency.is_not(None),history.c.currency!=currency),
        sa.and_(p.c.currency.is_not(None),p.c.currency!=currency))
    scoped=sa.select(a.c.run_id,a.c.started_at,
        sa.func.greatest(sa.func.coalesce(r.c.amount,0),sa.func.coalesce(history.c.peak,0)).label('amount'),
        uncertain.label('uncertain')
    ).select_from(a.outerjoin(history,a.c.attempt_id==history.c.attempt_id)
        .outerjoin(r,sa.and_(r.c.attempt_id==a.c.attempt_id,r.c.tenant_id==a.c.tenant_id))
        .outerjoin(q,sa.and_(q.c.attempt_id==a.c.attempt_id,q.c.tenant_id==a.c.tenant_id))
        .outerjoin(p,sa.and_(p.c.price_id==q.c.price_id,p.c.tenant_id==a.c.tenant_id))
    ).where(a.c.tenant_id==tenant)
    if exclude is not None:
        scoped=scoped.where(a.c.attempt_id!=exclude)
    scoped=scoped.subquery()
    day=started_at.astimezone(timezone.utc).date()
    start=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc)
    in_day=sa.and_(scoped.c.started_at>=start,scoped.c.started_at<start+timedelta(days=1))
    in_run=scoped.c.run_id==run_id
    return db.execute(sa.select(
        sa.func.coalesce(sa.func.sum(scoped.c.amount),0).label('tenant'),
        sa.func.coalesce(sa.func.sum(scoped.c.amount).filter(in_day),0).label('day'),
        sa.func.coalesce(sa.func.sum(scoped.c.amount).filter(in_run),0).label('run'),
        sa.func.count().filter(in_run).label('run_requests'),
        sa.func.coalesce(sa.func.bool_or(scoped.c.uncertain),False).label('uncertain'))).mappings().one()


def reserve(db, attempt, request):
    # Caller holds tenant lock before creating attempt/quote; publication and
    # reconciliation use the same lock. This is not check-then-call in Python.
    policies=metadata.tables['model_budget_policies']
    policy=db.execute(sa.select(policies).where(policies.c.tenant_id==attempt['tenant_id'])
        .order_by(policies.c.version.desc()).limit(1)).mappings().first()
    runs=metadata.tables['agent_runs']
    run=db.execute(sa.select(runs.c.provider,runs.c.model).where(
        runs.c.tenant_id==attempt['tenant_id'],runs.c.id==attempt['run_id'])).mappings().one()
    if policy is None:
        if (run['provider'],run['model'])!=(attempt['provider'],attempt['model']):
            raise ModelBudgetError()
        return request[1] if request is not None else None
    if (policy['provider'],policy['model'])!=(run['provider'],run['model']):
        raise ModelBudgetError()
    route=None
    if (policy['provider'],policy['model'])!=(attempt['provider'],attempt['model']):
        routes=metadata.tables['model_route_budget_policies']
        route=db.execute(sa.select(routes).where(routes.c.tenant_id==attempt['tenant_id'],
            routes.c.provider==attempt['provider'],routes.c.model==attempt['model'])
            .order_by(routes.c.version.desc()).limit(1)).mappings().first()
        if route is None or route['currency']!=policy['currency']:
            raise ModelBudgetError()
    token_contract=route if route is not None else policy
    settings=bounded_settings(request,token_contract['output_token_limit'])
    p,q=(metadata.tables[n] for n in ('model_price_versions','model_price_quotes'))
    price=db.execute(sa.select(p).join(q,sa.and_(q.c.price_id==p.c.price_id,q.c.tenant_id==p.c.tenant_id))
        .where(q.c.attempt_id==attempt['attempt_id'],q.c.tenant_id==attempt['tenant_id'])).mappings().first()
    if price is None or price['currency']!=policy['currency']:
        raise ModelBudgetError()
    totals=exposure(db,attempt['tenant_id'],policy['currency'],run_id=attempt['run_id'],
        started_at=attempt['started_at'],exclude=attempt['attempt_id'])
    if totals['uncertain']:
        raise ModelBudgetError()
    with localcontext() as ctx:
        ctx.prec=50
        amount=((Decimal(token_contract['input_token_ceiling'])*max(price[n] for n in
            ('input_rate','cache_read_rate','cache_write_rate'))+
            Decimal(settings['max_tokens'])*price['output_rate'])/Decimal(1000000)
        ).quantize(Decimal('.000000001'),rounding=ROUND_UP)
        soft={scope+'_soft':totals[scope]+amount>=policy[scope+'_limit']*Decimal(policy['soft_percent'])/100
              for scope in ('tenant','day','run')}
        if (totals['run_requests']>=policy['max_run_requests'] or
            any(totals[scope]+amount>policy[scope+'_limit'] for scope in ('tenant','day','run'))):
            raise ModelBudgetExceeded()
    db.execute(metadata.tables['model_budget_reservations'].insert().values(
        tenant_id=attempt['tenant_id'],attempt_id=attempt['attempt_id'],policy_id=policy['policy_id'],
        route_policy_id=route['route_policy_id'] if route is not None else None,
        amount=amount,currency=policy['currency'],output_token_limit=settings['max_tokens'],
        reserved_at=sa.func.clock_timestamp(),**soft))
    return settings


class ModelBudgetRepository(ModelCostRepository):
    def route_policy(self, *, provider, model):
        if provider not in {'openai','local'} or not isinstance(model,str) or not 1<=len(model)<=200:
            raise ValueError('Invalid route budget identity')
        table=metadata.tables['model_route_budget_policies']
        with self.engine.begin() as db:
            actor=self._principal(db); tenant=uuid.UUID(actor.tenant_id)
            row=db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.provider==provider,table.c.model==model)
                .order_by(table.c.version.desc()).limit(1)).mappings().first()
            return wire(row) if row else None

    def publish_route_policy(self, *, publication_id, expected_version, provider, model,
                             currency, input_token_ceiling, output_token_limit, source_sha256):
        if (provider not in {'openai','local'} or not isinstance(model,str) or not 1<=len(model)<=200
            or currency not in {'CNY','USD'} or type(expected_version) is not int or expected_version<0
            or type(input_token_ceiling) is not int or not 1<=input_token_ceiling<=1000000000
            or type(output_token_limit) is not int or not 1<=output_token_limit<=1000000000):
            raise ValueError('Invalid route budget policy')
        publication=uuid.UUID(str(publication_id))
        values=dict(provider=provider,model=model,currency=currency,
            input_token_ceiling=input_token_ceiling,output_token_limit=output_token_limit,
            source_sha256=digest(source_sha256))
        table=metadata.tables['model_route_budget_policies']
        primary=metadata.tables['model_budget_policies']
        with self.engine.begin() as db:
            actor=self._principal(db,writing=True); tenant=uuid.UUID(actor.tenant_id)
            budget_lock(db,tenant)
            old=db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.publication_id==publication)).mappings().first()
            if old:
                if old['version']!=expected_version+1 or any(old[k]!=v for k,v in values.items()):
                    raise CostConflict('Route budget publication identifier conflict')
                return wire(old)
            latest_primary=db.execute(sa.select(primary.c.currency).where(primary.c.tenant_id==tenant)
                .order_by(primary.c.version.desc()).limit(1)).scalar_one_or_none()
            if latest_primary is None:
                raise ValueError('Primary tenant budget is required')
            if latest_primary!=currency:
                raise CostConflict('Route budget currency differs from tenant budget')
            previous=db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.provider==provider,table.c.model==model)
                .order_by(table.c.version.desc()).limit(1)).mappings().first()
            if (previous['version'] if previous else 0)!=expected_version:
                raise CostConflict('Route budget version changed')
            if previous is not None and previous['currency']!=currency:
                raise CostConflict('Route budget currency conversion is not implicit')
            row=db.execute(table.insert().values(route_policy_id=uuid.uuid4(),tenant_id=tenant,
                actor_id=uuid.UUID(actor.user_id),publication_id=publication,version=expected_version+1,
                published_at=sa.func.clock_timestamp(),**values).returning(table)).mappings().one()
            self._audit(db,actor,'cost.route_budget.publish',row['route_policy_id'],row['version'])
            return wire(row)

    def status(self):
        policies=metadata.tables['model_budget_policies']
        reservations=metadata.tables['model_budget_reservations']
        attempts=metadata.tables['model_usage_attempts']
        with self.engine.begin() as db:
            actor=self._principal(db); tenant=uuid.UUID(actor.tenant_id)
            policy=db.execute(sa.select(policies).where(policies.c.tenant_id==tenant)
                .order_by(policies.c.version.desc()).limit(1)).mappings().first()
            if policy is None:
                return {'enabled':False,'policy':None,'usage':None,'soft_events':{'tenant':0,'day':0,'run':0}}
            now=db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            totals=exposure(db,tenant,policy['currency'],run_id=uuid.UUID(int=0),started_at=now)
            soft_events={
                'tenant':db.execute(sa.select(sa.func.count(sa.distinct(reservations.c.tenant_id))).where(
                    reservations.c.tenant_id==tenant,reservations.c.tenant_soft)).scalar_one(),
                'day':db.execute(sa.select(sa.func.count()).select_from(sa.select(
                    sa.cast(sa.func.timezone('UTC',attempts.c.started_at),sa.Date)).select_from(
                    reservations.join(attempts,sa.and_(reservations.c.tenant_id==attempts.c.tenant_id,
                        reservations.c.attempt_id==attempts.c.attempt_id))).where(
                    reservations.c.tenant_id==tenant,reservations.c.day_soft).distinct().subquery())).scalar_one(),
                'run':db.execute(sa.select(sa.func.count(sa.distinct(attempts.c.run_id))).select_from(
                    reservations.join(attempts,sa.and_(reservations.c.tenant_id==attempts.c.tenant_id,
                        reservations.c.attempt_id==attempts.c.attempt_id))).where(
                    reservations.c.tenant_id==tenant,reservations.c.run_soft)).scalar_one()}
            values={scope:None if totals['uncertain'] else format(totals[scope],'.9f') for scope in ('tenant','day')}
            remaining={scope:None if totals['uncertain'] else format(max(Decimal(0),policy[scope+'_limit']-totals[scope]),'.9f')
                       for scope in ('tenant','day')}
            public_policy={name:(format(policy[name],'.9f') if name.endswith('_limit') else policy[name]) for name in
                ('version','provider','model','currency','tenant_limit','day_limit','run_limit',
                 'input_token_ceiling','output_token_limit','max_run_requests','soft_percent','published_at')}
            return {'enabled':True,'policy':wire(public_policy),'usage':{
                'tenant_amount':values['tenant'],'day_amount':values['day'],
                'tenant_remaining':remaining['tenant'],'day_remaining':remaining['day'],
                'unresolved':bool(totals['uncertain'])},'soft_events':soft_events}

    def publish(self, *, publication_id, expected_version, provider, model, currency,
                tenant_limit, day_limit, run_limit, input_token_ceiling, output_token_limit,
                max_run_requests, soft_percent, source_sha256):
        integers={'input_token_ceiling':(input_token_ceiling,1000000000),
            'output_token_limit':(output_token_limit,1000000000),'max_run_requests':(max_run_requests,10000),
            'soft_percent':(soft_percent,99)}
        if (any(type(v) is not int or not 1<=v<=maximum for v,maximum in integers.values())
            or type(expected_version) is not int or expected_version<0
            or provider not in {'deepseek','openai','local','synthetic'} or not isinstance(model,str)
            or not 1<=len(model)<=200 or currency not in {'CNY','USD'}):
            raise ValueError('Invalid budget policy')
        publication=uuid.UUID(str(publication_id))
        values={'provider':provider,'model':model,'currency':currency,
            'source_sha256':digest(source_sha256),**{n:v for n,(v,_) in integers.items()},
            **{n:money(v,maximum=Decimal('1000000000000000000')) for n,v in
               (('tenant_limit',tenant_limit),('day_limit',day_limit),('run_limit',run_limit))}}
        table=metadata.tables['model_budget_policies']
        with self.engine.begin() as db:
            actor=self._principal(db,writing=True); tenant=uuid.UUID(actor.tenant_id)
            budget_lock(db,tenant)
            old=db.execute(sa.select(table).where(table.c.tenant_id==tenant,
                table.c.publication_id==publication)).mappings().first()
            if old:
                if old['version']!=expected_version+1 or any(old[k]!=v for k,v in values.items()):
                    raise CostConflict('Budget publication identifier conflict')
                return wire(old)
            previous=db.execute(sa.select(table).where(table.c.tenant_id==tenant)
                .order_by(table.c.version.desc()).limit(1)).mappings().first()
            if (previous['version'] if previous else 0)!=expected_version:
                raise CostConflict('Budget policy version changed')
            if previous and previous['currency']!=currency:
                raise CostConflict('Budget currency conversion is not implicit')
            row=db.execute(table.insert().values(policy_id=uuid.uuid4(),tenant_id=tenant,
                actor_id=uuid.UUID(actor.user_id),publication_id=publication,version=expected_version+1,
                published_at=sa.func.clock_timestamp(),**values).returning(table)).mappings().one()
            self._audit(db,actor,'cost.budget.publish',row['policy_id'],row['version'])
            return wire(row)

    def reservations(self, *, run_id):
        run=uuid.UUID(str(run_id))
        a,r,runs=(metadata.tables[n] for n in ('model_usage_attempts','model_budget_reservations','agent_runs'))
        with self.engine.begin() as db:
            actor=self._principal(db); tenant=uuid.UUID(actor.tenant_id)
            if not db.execute(sa.select(runs.c.id).where(runs.c.id==run,runs.c.tenant_id==tenant)).first():
                from app.identity import AuthorizationError
                raise AuthorizationError('Budget Run unavailable')
            return [wire(row) for row in db.execute(sa.select(r).join(a,sa.and_(
                a.c.attempt_id==r.c.attempt_id,a.c.tenant_id==r.c.tenant_id)).where(
                r.c.tenant_id==tenant,a.c.run_id==run).order_by(r.c.reserved_at,r.c.attempt_id)).mappings()]
