"""Exact money and real disposable PostgreSQL; all prices/invoices are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4, UUID
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from pydantic_ai.usage import RequestUsage

from app.persistence.model_cost_repository import money, estimate, ModelCostRepository, CostConflict
from app.persistence.schema import metadata
from app.identity import AuthorizationError, AuthenticationError
from app.recovery import RecoveryHold
from app.maintenance import Maintenance
from app.backup import database_manifest, create_backup, restore_backup
from app.persistence.bundle import PostgreSQLRepositories
from app.database import utc_now
from app.run_leases import RunLease
from test_model_usage import prepare, queue_setup, postgres_database, backup_setup, empty_database


def publish(costs,*,version=0,publication_id=None,rate='2',currency='CNY'):
    return costs.publish_price(publication_id=publication_id or str(uuid4()),expected_version=version,
        provider='deepseek',model='synthetic',currency=currency,source_sha256='a'*64,
        input_rate=rate,output_rate='8',cache_read_rate='1',cache_write_rate='3')


def test_exposure_unknown_not_zero_revisions_not_summed_and_scope(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    assert costs.exposure(run_id=rid)==[]
    publish(costs)
    attempt=str(uuid4()); meter.begin(attempt)
    rows=costs.exposure(run_id=rid)
    assert rows==[dict(currency='CNY',attempts=1,unresolved=1,retained_amount='0.000000000',amount=None)]
    meter.finish(attempt,'completed',RequestUsage(input_tokens=100,output_tokens=10))
    assert costs.exposure(run_id=rid)[0]['amount']=='0.000280000'
    costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=0,
        amount='2',currency='CNY',receipt_sha256='b'*64)
    costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=1,
        amount='1',currency='CNY',receipt_sha256='c'*64)
    assert costs.exposure(run_id=rid)[0]['amount']=='2.000000000'
    pending=str(uuid4()); meter.begin(pending)
    costs.reconcile(attempt_id=pending,request_id=str(uuid4()),expected_revision=0,
        amount='3',currency='CNY',receipt_sha256='d'*64)
    rows=costs.exposure(run_id=rid)
    assert rows==[dict(currency='CNY',attempts=2,unresolved=1,retained_amount='5.000000000',amount=None)]
    today=datetime.now(timezone.utc).date()
    assert costs.exposure(utc_day=today)==rows
    assert costs.exposure(utc_day=date(2000,1,1))==[]
    with pytest.raises(ValueError):
        costs.exposure(utc_day=datetime.now(timezone.utc))
    with pytest.raises(AuthorizationError):
        costs.exposure(run_id=str(uuid4()))
    customer=bundle.config.authenticated(tokens['synthetic.customer']).model_costs()
    with pytest.raises(AuthorizationError):
        customer.exposure()


def test_exposure_unknown_currency_and_separate_currencies(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    missing=str(uuid4()); meter.begin(missing)
    meter.finish(missing,'error')
    assert costs.exposure()==[dict(currency=None,attempts=1,unresolved=1,retained_amount='0.000000000',amount=None)]
    publish(costs)
    cny=str(uuid4()); meter.begin(cny)
    meter.finish(cny,'completed',RequestUsage(input_tokens=100))
    publish(costs,version=1,currency='USD')
    usd=str(uuid4()); meter.begin(usd)
    meter.finish(usd,'completed',RequestUsage(input_tokens=100))
    rows=costs.exposure()
    assert {r['currency']:r['amount'] for r in rows}=={'CNY':'0.000200000','USD':'0.000200000',None:None}


def test_decimal_only_precision_and_cache_cost_unknown_boundaries():
    for bad in (1,0.1,True,'NaN','Infinity','1e-9','-1','0.0000000001','1000001',Decimal('NaN')):
        with pytest.raises(ValueError):
            money(bad)
    price={'currency':'CNY','input_rate':Decimal('2'),'output_rate':Decimal('8'),
           'cache_read_rate':Decimal('1'),'cache_write_rate':Decimal('3')}
    usage={'state':'completed','usage_status':'observed','input_tokens':100,'output_tokens':10,
           'cache_read_tokens':20,'cache_write_tokens':10}
    assert estimate(usage,price)['amount']==Decimal('0.000270000')
    assert estimate(usage,None)['amount'] is None
    assert estimate({**usage,'usage_status':'partial'},price)['reason']=='partial_usage'
    assert estimate({**usage,'cache_read_tokens':95},price)['reason']=='invalid_cache'
    assert estimate({**usage,'usage_status':'unknown'},price)['amount'] is None
    assert estimate(usage,{**price,**{name:Decimal('0') for name in ('input_rate','output_rate','cache_read_rate','cache_write_rate')}})['amount']==Decimal('0')


def test_price_pinned_before_call_corrections_do_not_reprice_old_attempt_and_audit_atomic(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    publication=str(uuid4()); first=publish(costs,publication_id=publication)
    assert publish(costs,publication_id=publication)['price_id']==first['price_id']
    with pytest.raises(CostConflict):
        publish(costs,publication_id=publication,rate='4')
    attempt=str(uuid4()); meter.begin(attempt)
    second=publish(costs,version=1,rate='20')
    meter.finish(attempt,'completed',RequestUsage(input_tokens=100,output_tokens=10,cache_read_tokens=20,cache_write_tokens=10))
    history=costs.history(attempt)
    assert len(history)==1 and history[0]['amount']==Decimal('.000270000')
    with bundle.engine.connect() as db:
        quote=db.execute(sa.select(metadata.tables['model_price_quotes']).where(
            metadata.tables['model_price_quotes'].c.attempt_id==UUID(attempt))).mappings().one()
        assert str(quote['price_id'])==first['price_id']!=second['price_id']
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['audit_logs']).where(
            metadata.tables['audit_logs'].c.action=='cost.price.publish')).scalar_one()==2
    with pytest.raises(CostConflict,match='version changed'):
        publish(costs,version=0)


def test_missing_and_partial_usage_remain_unknown_even_with_explicit_free_prices(queue_setup):
    bundle,*_,meter=prepare(queue_setup)
    tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    no_price=str(uuid4()); meter.begin(no_price)
    meter.finish(no_price,'completed',RequestUsage(input_tokens=2))
    assert costs.history(no_price)[0]['reason']=='no_price'
    publish(costs,rate='0')
    zero=str(uuid4()); meter.begin(zero); meter.finish(zero,'completed',RequestUsage())
    partial=str(uuid4()); meter.begin(partial); meter.finish(partial,'cancelled',RequestUsage(input_tokens=2))
    assert costs.history(zero)[0]['reason']=='unknown_usage'
    assert costs.history(partial)[0]['reason']=='partial_usage'
    assert all(row['amount'] is None for a in (zero,partial) for row in costs.history(a))


def test_append_reconciliation_idempotency_revision_receipt_and_currency_guards(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    publish(costs)
    attempt=str(uuid4()); meter.begin(attempt)
    # Unknown crashed/pending request can be annotated without mutating usage.
    request=str(uuid4())
    value=dict(attempt_id=attempt,request_id=request,expected_revision=0,amount='0.002',currency='CNY',receipt_sha256='b'*64)
    first=costs.reconcile(**value)
    assert costs.reconcile(**value)['assessment_id']==first['assessment_id']
    with pytest.raises(CostConflict):
        costs.reconcile(**{**value,'amount':'0.003'})
    with pytest.raises(CostConflict,match='changed'):
        costs.reconcile(**{**value,'request_id':str(uuid4()),'receipt_sha256':'c'*64})
    with pytest.raises(CostConflict,match='Currency'):
        costs.reconcile(**{**value,'request_id':str(uuid4()),'expected_revision':1,'currency':'USD','receipt_sha256':'c'*64})
    corrected=costs.reconcile(**{**value,'request_id':str(uuid4()),'expected_revision':1,'amount':'0.001','receipt_sha256':'c'*64})
    assert corrected['revision']==2
    meter.finish(attempt,'error')
    history=costs.history(attempt)
    assert [row['revision'] for row in history]==[0,1,2]
    assert history[0]['status']=='unknown' and history[-1]['status']=='operator_reported'
    assert meter.list_attempts()[0]['state']=='error'
    another=str(uuid4()); meter.begin(another)
    with pytest.raises(CostConflict,match='Receipt'):
        costs.reconcile(**{**value,'request_id':str(uuid4()),'attempt_id':another})


def test_concurrent_reconciliation_only_one_expected_revision_wins(queue_setup):
    bundle,*_,meter=prepare(queue_setup); tokens=queue_setup[3]
    attempt=str(uuid4()); meter.begin(attempt)
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    def append(index):
        try:
            return costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=0,
                amount='1.001',currency='CNY',receipt_sha256=str(index+1)*64)['revision']
        except CostConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(append,range(2)))
    assert results.count(1)==1 and results.count('conflict')==1
    assert len(costs.history(attempt))==1


def test_concurrent_price_revision_and_audit_failure_roll_back_atomically(queue_setup,monkeypatch):
    bundle=queue_setup[0]; tokens=queue_setup[3]
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    original=costs._audit
    def unavailable(*args):
        raise RuntimeError('Synthetic audit unavailable')
    monkeypatch.setattr(costs,'_audit',unavailable)
    with pytest.raises(RuntimeError):
        publish(costs)
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(metadata.tables['model_price_versions'])).scalar_one()==0
    monkeypatch.setattr(costs,'_audit',original)
    def publish_current(_):
        try:
            return publish(costs)['version']
        except CostConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(publish_current,range(2)))
    assert results.count(1)==1 and results.count('conflict')==1


def test_role_tenant_revocation_holds_and_sql_immutability(queue_setup):
    bundle,root,owner,tokens,sid,rid,job,chat,meter=prepare(queue_setup)
    costs=bundle.config.authenticated(tokens['synthetic.admin']).model_costs()
    attempt=str(uuid4()); meter.begin(attempt); meter.finish(attempt,'completed',RequestUsage(input_tokens=1))
    with pytest.raises(AuthorizationError):
        publish(bundle.config.authenticated(tokens['synthetic.customer']).model_costs())
    tenant=bundle.identity.create_tenant('Synthetic other cost tenant')
    bundle.identity.provision_user('synthetic.cost.other','synthetic-password-only',tenant,role='admin')
    other=bundle.identity.login('synthetic.cost.other','synthetic-password-only',tenant).token
    other_costs=ModelCostRepository(bundle.engine,token=other)
    assert other_costs.exposure()==[]
    with pytest.raises(AuthorizationError):
        other_costs.exposure(run_id=rid)
    with pytest.raises(AuthorizationError):
        other_costs.history(attempt)
    with pytest.raises(AuthorizationError):
        other_costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=0,
            amount='1',currency='CNY',receipt_sha256='b'*64)
    for name in ('model_usage_attempts','model_price_versions','model_price_quotes','model_cost_assessments'):
        with bundle.engine.begin() as db:
            with pytest.raises(sa.exc.DBAPIError,match='append-only'):
                db.exec_driver_sql('TRUNCATE public.'+name+' CASCADE')
    for name in ('model_price_quotes','model_cost_assessments'):
        with bundle.engine.begin() as db:
            with pytest.raises(sa.exc.DBAPIError,match='append-only'):
                db.execute(metadata.tables[name].delete())
    manager=Maintenance(bundle.engine); permit=manager.request()
    try:
        with pytest.raises(Exception,match='maintenance'):
            publish(costs)
    finally:
        manager.release(permit)
    bundle.identity.logout(tokens['synthetic.admin'])
    with pytest.raises(AuthenticationError):
        costs.history(attempt)
    with pytest.raises(AuthenticationError):
        costs.exposure()


def test_null_receipt_or_currency_cannot_bypass_sql_check(queue_setup):
    bundle,*_,meter=prepare(queue_setup)
    attempt=str(uuid4()); meter.begin(attempt)
    rows=meter.list_attempts(); tenant=UUID(rows[0]['tenant_id']); actor=UUID(rows[0]['actor_id'])
    for currency,receipt in ((None,'b'*64),('CNY',None)):
        with bundle.engine.begin() as db:
            with pytest.raises(sa.exc.IntegrityError):
                db.execute(metadata.tables['model_cost_assessments'].insert().values(assessment_id=uuid4(),tenant_id=tenant,
                    attempt_id=UUID(attempt),revision=1,kind='reconcile',status='operator_reported',reason='operator_statement',
                    amount=Decimal('1'),currency=currency,receipt_sha256=receipt,request_id=uuid4(),actor_id=actor,created_at=sa.func.clock_timestamp()))


def test_actual_backup_restores_prices_quotes_and_reconciliations_without_replay(backup_setup,tmp_path):
    bundle,common,package,tenant,token,_=backup_setup
    costs=bundle.config.authenticated(token).model_costs(); publish(costs)
    sid,rid,owner=str(uuid4()),str(uuid4()),str(uuid4())
    chat=bundle.chat.authenticated(token)
    assert chat.start_run(session_id=sid,run_id=rid,user_id=str(uuid4()),title='Synthetic',content='Synthetic',
        provider='deepseek',model='synthetic',prompt_version_id=None,now=utc_now(),lease_owner=owner)
    bound=chat.with_lease(RunLease(rid,owner,1)); meter=bound.model_meter(rid,provider='deepseek',model='synthetic')
    attempt=str(uuid4()); meter.begin(attempt); meter.finish(attempt,'completed',RequestUsage(input_tokens=100))
    costs.reconcile(attempt_id=attempt,request_id=str(uuid4()),expected_revision=0,amount='0.0002',currency='CNY',receipt_sha256='b'*64)
    bound.finish_run(rid,'complete',None,utc_now())
    names=('model_usage_attempts','model_price_versions','model_price_quotes','model_cost_assessments')
    with bundle.engine.connect() as db:
        manifest=database_manifest(db); expected={name:manifest['tables'][name] for name in names}
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        options={**common,'url':url,'knowledge_root':tmp_path/'restored-knowledge','asset_root':tmp_path/'restored-assets'}
        assert restore_backup(**options,source=package,isolated_confirmed=True)['verified']
        restored=PostgreSQLRepositories.from_url(url,pool_size=1)
        try:
            with restored.engine.connect() as db:
                actual=database_manifest(db)
                assert {name:actual['tables'][name] for name in names}==expected
                rows=db.execute(sa.select(metadata.tables['model_cost_assessments']).order_by(
                    metadata.tables['model_cost_assessments'].c.revision)).mappings().all()
                assert [row['status'] for row in rows]==['estimated','operator_reported']
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one() is True
        finally:
            restored.close()
