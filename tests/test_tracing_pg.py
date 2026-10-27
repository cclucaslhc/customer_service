import json
from pathlib import Path
import subprocess
import sys
import uuid

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest
import sqlalchemy as sa

from app.auth_http import AuthPolicy
from app.main import create_app
from app.security import SecretStore
from app.tracing import SafeTracing, capture_carrier
from test_durable_chat import queue_setup, submit
from test_postgresql_integration import postgres_database


def test_internal_trace_carrier_sql_validation_and_idempotency(queue_setup):
    bundle, root, owner, tokens = queue_setup
    exporter=InMemorySpanExporter(); tracing=SafeTracing(exporter)
    sid,rid=str(uuid.uuid4()),str(uuid.uuid4())
    q=root.tables['chat_run_queue']
    try:
        def enqueue(carrier):
            owner.enqueue(session_id=sid,request_id=rid,content='Synthetic body',title='Synthetic',
                provider='deepseek',model='synthetic',prompt_version_id=None,instructions='Synthetic',trace_context=carrier)
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())):
            first=capture_carrier(); enqueue(first)
        with tracing.scope('http.request',root=True,request_id=str(uuid.uuid4())):
            second=capture_carrier(); enqueue(second)
        with bundle.engine.connect() as db:
            assert db.execute(sa.select(q.c.trace_context).where(q.c.run_id==uuid.UUID(rid))).scalar_one()==first
        assert first != second
        for bad in ({**first,'prompt':'SECRET'}, {**first,'sampled':'true'},
                    {**first,'trace_id':'0'*32}, {**first,'request_id':'SECRET'},
                    {**first,'span_id':None}, {**first,'trace_id':'F'*32}):
            with pytest.raises(sa.exc.IntegrityError):
                with bundle.engine.begin() as db:
                    db.execute(q.update().where(q.c.run_id==uuid.UUID(rid)).values(trace_context=bad))
        job=root.claim_next()
        assert job['trace_context']==first
        # Untraced old-style submit remains accepted; no invented past context.
        _, old_rid=submit(owner)
        with bundle.engine.connect() as db:
            assert db.execute(sa.select(q.c.trace_context).where(q.c.run_id==uuid.UUID(old_rid))).scalar_one() is None
        assert any(item['name'].endswith('queue_trace_context')
                   for item in sa.inspect(bundle.engine).get_check_constraints('chat_run_queue'))
    finally:
        tracing.close()


def test_actual_authenticated_api_to_independent_worker_and_agent_trace(queue_setup,tmp_path):
    bundle, root, owner, tokens = queue_setup
    exporter=InMemorySpanExporter(); tracing=SafeTracing(exporter)
    paths={key:tmp_path/key for key in ('knowledge_dir','asset_dir','legacy_upload_dir')}
    configured_model=bundle.config.authenticated(tokens['synthetic.admin']).provider('deepseek')['model']
    price=bundle.config.authenticated(tokens['synthetic.admin']).model_costs().publish_price(
        publication_id=str(uuid.uuid4()),expected_version=0,provider='deepseek',model=configured_model,currency='CNY',
        source_sha256='a'*64,input_rate='2',output_rate='8',cache_read_rate='1',cache_write_rate='3')
    policy=AuthPolicy('https://localhost')
    app=create_app(repositories=bundle,secret_store=SecretStore(path=tmp_path/'unused.key'),
                   knowledge_paths=paths,auth_policy=policy,durable_chat=True,tracing=tracing)
    url=bundle.engine.url.render_as_string(hide_password=False)
    tenant=bundle.identity.resolve(tokens['synthetic.customer']).tenant_id
    evidence=tmp_path/'synthetic-traces.jsonl'
    try:
        with TestClient(app,base_url=policy.origin,client=('127.0.0.1',50000)) as client:
            login=client.post('/api/auth/login',json={'tenant_id':tenant,'username':'synthetic.customer',
                'password':'synthetic-password-only'},headers={'Origin':policy.origin})
            assert login.status_code==200
            headers={'Origin':policy.origin,'X-CSRF-Token':login.json()['csrf_token']}
            session=client.post('/api/chat/sessions',headers=headers).json()
            result=client.post(f'/api/chat/sessions/{session["id"]}/messages',headers={**headers,
                'traceparent':'00-'+'a'*32+'-'+'b'*16+'-01','baggage':'SECRET_BAGGAGE'},
                json={'content':'SECRET_SYNTHETIC_BODY'})
            assert result.status_code==202
            rid=result.json()['run_id']
            process=subprocess.run([sys.executable,str(Path('scripts/chat_worker.py').resolve()),
                '--database-url',url,'--master-key-path',str(tmp_path/'unused-worker.key'),
                '--knowledge-root',str(paths['knowledge_dir']),'--asset-root',str(paths['asset_dir']),
                '--legacy-root',str(paths['legacy_upload_dir']),'--once','--synthetic',
                '--synthetic-trace-output',str(evidence)],capture_output=True,text=True,timeout=30)
            assert process.returncode==0,process.stderr
            state=client.get(f'/api/chat/sessions/{session["id"]}/runs/{rid}').json()
            # Snapshot route's public envelope is not the private carrier.
            assert 'trace_context' not in json.dumps(state)
            q=root.tables['chat_run_queue']
            with bundle.engine.connect() as db:
                carrier=db.execute(sa.select(q.c.trace_context).where(q.c.run_id==uuid.UUID(rid))).scalar_one()
                status=db.execute(sa.select(root.tables['agent_runs'].c.status).where(
                    root.tables['agent_runs'].c.id==uuid.UUID(rid))).scalar_one()
            assert status=='complete'
            assert carrier['request_id']==result.headers['X-Request-ID']
            assert tracing.flush()
            api_span=next(span for span in exporter.get_finished_spans()
                          if span.name=='http.request' and span.attributes.get('request_id')==result.headers['X-Request-ID'])
            children=[json.loads(line) for line in evidence.read_text(encoding='utf-8').splitlines()]
            by_name={row['name']:row for row in children}
            assert set(by_name)=={'worker.chat','agent.run','model.request','db.query'}
            queries=[row for row in children if row['name']=='db.query']
            assert queries and all(set(row['attributes']) <= {'request_id','run_id','attempt_id','outcome'} for row in queries)
            assert all(row['trace_id']==f'{api_span.context.trace_id:032x}' for row in queries)
            worker,agent=by_name['worker.chat'],by_name['agent.run']
            assert worker['trace_id']==agent['trace_id']==f'{api_span.context.trace_id:032x}'
            assert worker['trace_id']!='a'*32
            assert worker['parent_id']==f'{api_span.context.span_id:016x}'
            assert agent['parent_id']==worker['span_id']
            model=by_name['model.request']
            assert model['parent_id']==agent['span_id']
            assert model['trace_id']==agent['trace_id']
            assert model['attributes']['outcome']=='completed'
            assert model['attributes']['model.stream'] is True
            assert uuid.UUID(model['attributes']['attempt_id'])
            usage=root.tables['model_usage_attempts']
            with bundle.engine.connect() as db:
                row=db.execute(sa.select(usage).where(usage.c.run_id==uuid.UUID(rid))).mappings().one()
            assert str(row['attempt_id'])==model['attributes']['attempt_id']
            assert row['state']=='completed'
            assert row['usage_status'] in ('observed','unknown')
            assert str(row['tenant_id'])==tenant
            quotes,costs=(root.tables[name] for name in ('model_price_quotes','model_cost_assessments'))
            with bundle.engine.connect() as db:
                quote=db.execute(sa.select(quotes).where(quotes.c.attempt_id==row['attempt_id'])).mappings().one()
                cost=db.execute(sa.select(costs).where(costs.c.attempt_id==row['attempt_id'])).mappings().one()
            assert str(quote['price_id'])==price['price_id']
            assert cost['revision']==0 and cost['kind']=='estimate'
            assert cost['status']=='estimated' if row['usage_status']=='observed' else cost['status']=='unknown'
            assert all(row['attributes']['run_id']==rid and row['attributes']['request_id']==carrier['request_id'] for row in children)
            assert 'SECRET_' not in evidence.read_text(encoding='utf-8')
    finally:
        tracing.close()
