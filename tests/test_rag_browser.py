"""Opt-in live browser harness on disposable PG; never a production entrypoint.

Run with TEST_RAG_BROWSER=1 and TEST_POSTGRES_URL pointing to owned test PG.
The harness prints its ephemeral loopback URL, synthetic login and stop-file
path. Operate the actual UI twice, then create the stop file to verify/close.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import socket
import threading
import time
import uuid
import pytest
import sqlalchemy as sa
from pydantic_ai.models.function import FunctionModel,DeltaToolCall
from pydantic_ai.messages import ToolReturnPart,UserPromptPart
from app.auth_http import AuthPolicy
from app.chat_worker import ChatWorker
from app.database import utc_now
from app.persistence.schema import metadata
from app.prompting import combined_instructions,DEFAULT_BUSINESS_PROMPT
from app.providers import ModelGateway,ProviderSnapshot
from app.security import SecretStore
from test_rag_routed_retrieval import vector_rollout,Embedding,CONTENT,candidate_session,planned,release,queue_setup,postgres_database


@pytest.mark.skipif(os.environ.get('TEST_RAG_BROWSER')!='1',reason='Opt-in live manual browser acceptance')
@pytest.mark.parametrize('vector_rollout',[{'instructions':combined_instructions(DEFAULT_BUSINESS_PROMPT),
    'lifetime_seconds':3600}],indirect=True)
def test_live_browser_gray_answer_and_pause(vector_rollout,tmp_path,monkeypatch):
    import uvicorn
    import app.main as main_module
    bundle,controller,control,tokens,_,_,candidate=vector_rollout
    admin=bundle.identity.resolve(tokens[0])
    bundle.config.authenticated(tokens[0]).update_provider('deepseek',model='synthetic',now=utc_now())
    sid=candidate_session(control)
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['chat_sessions'].insert().values(id=uuid.UUID(sid),
            tenant_id=uuid.UUID(admin.tenant_id),owner_id=uuid.UUID(admin.user_id),
            title='浏览器灰度验收（合成）',created_at=utc_now(),updated_at=utc_now()))
    paused=False
    async def stream(messages,info):
        nonlocal paused
        if any(isinstance(part,ToolReturnPart) for message in messages for part in message.parts):
            latest=next((str(part.content) for message in reversed(messages) for part in reversed(message.parts)
                         if isinstance(part,UserPromptPart)),'')
            if '暂停' in latest and not paused:
                await asyncio.to_thread(controller.pause,**control)
                paused=True
            yield CONTENT
        else:
            yield {0:DeltaToolCall(name='search_knowledge',json_args='{"query":"退货订单号"}',tool_call_id='synthetic-browser-search')}
    class Gateway(ModelGateway):
        def snapshot(self):
            return ProviderSnapshot('deepseek','synthetic','unused')
        def build_model(self,snapshot):
            return FunctionModel(stream_function=stream)
    monkeypatch.setattr(main_module,'ModelGateway',Gateway)
    secrets=SecretStore(path=tmp_path/'unused.key')
    paths={'knowledge_dir':tmp_path/'knowledge','asset_dir':tmp_path/'assets','legacy_upload_dir':tmp_path/'legacy'}
    sock=socket.socket()
    # Separate loopback host keeps the synthetic cookie away from the user's
    # ordinary 127.0.0.1 development sessions (cookies are not port-scoped).
    sock.bind(('127.0.0.87',0))
    port=sock.getsockname()[1]
    origin=f'http://127.0.0.87:{port}'
    policy=AuthPolicy(origin,secure_cookie=False)
    app=main_module.create_app(repositories=bundle,secret_store=secrets,knowledge_paths=paths,
        auth_policy=policy,durable_chat=True)
    # Test-only preauthenticated storage state, not a production auth route.
    # The caller account/session was provisioned by the disposable fixture;
    # all business HTTP and SSE still pass the unchanged cookie/CSRF boundary.
    from fastapi.routing import APIRoute
    from fastapi.responses import RedirectResponse
    def entry():
        response=RedirectResponse(f'/?session={sid}')
        response.set_cookie(policy.cookie_name,tokens[0],max_age=3600,secure=False,
            httponly=True,samesite='strict',path='/')
        return response
    app.router.routes.insert(0,APIRoute('/__browser_fixture_entry',entry,methods=['GET']))
    worker=ChatWorker(bundle,secrets,paths,rag_embedding_gateway=Embedding(),
        gateway_factory=lambda credential,job:Gateway(secrets,repository=bundle.config.authenticated(credential)))
    original_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            stop=asyncio.Event()
            task=asyncio.create_task(worker.run(stop))
            try:
                yield
            finally:
                stop.set()
                await asyncio.wait_for(task,10)
    app.router.lifespan_context=lifespan
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.87',port=port,log_level='warning',timeout_graceful_shutdown=5))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    stop_file=tmp_path/'browser-stop'
    try:
        deadline=time.monotonic()+15
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.05)
        print('BROWSER_READY '+json.dumps({'url':origin+'/__browser_fixture_entry',
            'tenant_id':admin.tenant_id,'session_id':sid,
            'stop_file':str(stop_file),'instructions':'Use seeded session. Send 退货需要什么？ then 暂停测试：退货需要什么？'},ensure_ascii=False),flush=True)
        deadline=time.monotonic()+900
        while not stop_file.exists():
            assert thread.is_alive() and time.monotonic()<deadline,'Browser acceptance timeout'
            time.sleep(.2)
        with bundle.engine.connect() as db:
            runs=metadata.tables['agent_runs']
            rows=db.execute(sa.select(runs).where(runs.c.session_id==uuid.UUID(sid)).order_by(runs.c.started_at)).mappings().all()
            assert [row['status'] for row in rows]==['complete','interrupted']
            assert all(row['rag_route']['index_id']==candidate for row in rows)
            messages=metadata.tables['messages']
            answers=db.execute(sa.select(messages.c.content).where(messages.c.session_id==uuid.UUID(sid),messages.c.role=='assistant')).scalars().all()
            assert answers==[CONTENT]
            events=metadata.tables['chat_run_events']
            final_tokens=db.execute(sa.select(events.c.data).where(events.c.run_id==rows[0]['id'],
                events.c.event=='token').order_by(events.c.sequence)).scalars().all()
            assert ''.join(item['text'] for item in final_tokens)==CONTENT
            assert not db.execute(sa.select(events.c.sequence).where(events.c.run_id==rows[-1]['id'],events.c.event=='token')).all()
        assert paused and not (tmp_path/'unused.key').exists()
        print('BROWSER_VERIFIED '+json.dumps({'statuses':['complete','interrupted'],'candidate_runs':2,
            'assistant_messages':1,'paused_run_final_tokens':0,'real_model_calls':0}),flush=True)
    finally:
        server.should_exit=True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive()
