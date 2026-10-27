"""Local browser acceptance with temporary data and deterministic model; no keys."""

import sys
import tempfile
import argparse
import os
import uuid
import subprocess
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@contextmanager
def synthetic_storage_policy(enabled):
    if not enabled:
        yield None
        return
    import boto3
    from botocore.config import Config
    from app.storage_config import StoragePolicy, StorageTarget
    client = boto3.client('s3',endpoint_url='http://127.0.0.1:59000',region_name='us-east-1',
        aws_access_key_id='synthetic-s3-access',aws_secret_access_key='synthetic-s3-secret-only',
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},proxies={},
            connect_timeout=3,read_timeout=5,retries={'total_max_attempts':1}))
    bucket = 'cs-browser-s3-' + uuid.uuid4().hex
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket,VersioningConfiguration={'Status':'Enabled'})
    try:
        yield StoragePolicy([StorageTarget('approved','http://127.0.0.1:59000',bucket,'us-east-1')],loopback_tests=True)
    finally:
        objects = client.list_object_versions(Bucket=bucket)
        assert not objects.get('IsTruncated')
        entries = [{'Key':r['Key'],'VersionId':r['VersionId']} for r in objects.get('Versions',[])+objects.get('DeleteMarkers',[])]
        if entries:
            client.delete_objects(Bucket=bucket,Delete={'Objects':entries})
        client.delete_bucket(Bucket=bucket)
        client.close()


@contextmanager
def postgres_repositories(url):
    if not url:
        yield None
        return
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import make_url
    from app.persistence.bundle import PostgreSQLRepositories
    from app.persistence.engine import build_engine

    parsed = make_url(url)
    if (parsed.drivername != "postgresql+psycopg" or parsed.host not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username != "integration" or parsed.password != "local-integration-only" or parsed.database != "postgres"):
        raise ValueError("Only the dedicated local synthetic integration administrator is allowed")
    name = "cs_browser_" + uuid.uuid4().hex
    admin = build_engine(url, pool_size=1)
    created = False
    bundle = None
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
            db.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        target = parsed.set(database=name).render_as_string(hide_password=False)
        extension_engine = build_engine(target,pool_size=1)
        try:
            with extension_engine.begin() as db:
                db.exec_driver_sql('CREATE EXTENSION vector')
        finally:
            extension_engine.dispose()
        os.environ["MIGRATION_DATABASE_URL"] = target
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        bundle = PostgreSQLRepositories.from_url(target, pool_size=3)
        yield bundle
    finally:
        if bundle:
            bundle.close()
        if created:
            assert name.startswith("cs_browser_") and len(name) == 43
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
                db.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def main():
    import uvicorn
    import app.settings as settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-admin-url", help="Dedicated local synthetic integration administrator only")
    parser.add_argument("--auth-management", action="store_true", help="Synthetic cookie management browser acceptance only")
    parser.add_argument("--durable-chat", action="store_true", help="Separate synthetic Worker and persistent SSE acceptance")
    parser.add_argument('--durable-knowledge',action='store_true',help='Independent real-parser Worker on disposable synthetic files')
    parser.add_argument('--knowledge-queue-only',action='store_true',help='Synthetic pending/cancel UI acceptance without starting a knowledge Worker')
    parser.add_argument("--synthetic-delay", type=float, default=1, help="Durable synthetic Worker delay for recovery/cancel acceptance")
    parser.add_argument('--expire-terminal-events',action='store_true',help='Synthetic fault: prune completed Run events before subscribers receive done')
    parser.add_argument('--recovery-gate-acceptance',action='store_true',help='Synthetic hold and owned shutdown outside the production HTTP gate')
    parser.add_argument('--storage-acceptance',action='store_true',help='Owned loopback bucket and storage UI; no production credentials')
    args = parser.parse_args()
    if args.auth_management and not args.postgres_admin_url:
        parser.error("--auth-management requires isolated PostgreSQL")
    if args.durable_chat and not args.auth_management:
        parser.error("--durable-chat requires --auth-management")
    if args.durable_knowledge and not args.auth_management:
        parser.error('--durable-knowledge requires --auth-management')
    if args.knowledge_queue_only and not args.durable_knowledge:
        parser.error('--knowledge-queue-only requires --durable-knowledge')
    if args.recovery_gate_acceptance and not args.auth_management:
        parser.error('--recovery-gate-acceptance requires --auth-management')
    if args.storage_acceptance and (not args.auth_management or args.durable_knowledge):
        parser.error('Storage UI acceptance requires authentication and no production CLI parser Worker')
    if not 0 <= args.synthetic_delay <= 120:
        parser.error("--synthetic-delay must be between 0 and 120 seconds")
    if args.expire_terminal_events and not args.durable_chat:
        parser.error('--expire-terminal-events requires isolated durable chat')

    with tempfile.TemporaryDirectory(prefix="cs-browser-") as directory:
        root = Path(directory)
        settings.DATA_DIR = root
        settings.DATABASE_PATH = root / "app.db"
        settings.KNOWLEDGE_DIR = root / "knowledge"
        settings.KNOWLEDGE_ASSET_DIR = root / "knowledge_assets"
        settings.LEGACY_UPLOAD_DIR = root / "uploads"
        settings.UPLOAD_DIR = root / "knowledge"
        settings.SECRETS_DIR = root / "secrets"
        settings.MASTER_KEY_PATH = root / "secrets" / "master.key"

        from pydantic_ai.models.function import FunctionModel
        from app.providers import ProviderSnapshot
        from app.main import create_app
        from app.security import SecretStore

        async def model_stream(messages, info):
            import asyncio
            for piece in ["这是隔离验收回答。", "会话通过 Repository 保存。"]:
                await asyncio.sleep(0.2)
                yield piece

        class Gateway:
            def snapshot(self):
                return ProviderSnapshot("deepseek", "isolated-function-model", "not-a-key")

            def build_model(self, snapshot):
                return FunctionModel(stream_function=model_stream)

        with postgres_repositories(args.postgres_admin_url) as repositories, synthetic_storage_policy(args.storage_acceptance) as storage_policy:
            if args.expire_terminal_events:
                # This harness has already enforced the disposable local admin
                # identity. Never install this fault in the actual application.
                import sqlalchemy as sa
                from app.persistence.durable_chat_repository import DurableChatRepository
                assert repositories.engine.url.database.startswith('cs_browser_')
                original_read_events = DurableChatRepository.read_events
                def expire_result(queue,session_id,run_id,**kwargs):
                    result = original_read_events(queue,session_id,run_id,**kwargs)
                    if result and result['status'] == 'complete' and result['events']:
                        runs = queue.tables['agent_runs']
                        with queue.engine.begin() as db:
                            db.execute(runs.update().where(runs.c.id == uuid.UUID(run_id),runs.c.status == 'complete')
                                .values(finished_at=sa.func.clock_timestamp()-sa.text("interval '2 days'")))
                        DurableChatRepository(queue.engine).prune_events(retention_seconds=86400)
                        return original_read_events(queue,session_id,run_id,**kwargs)
                    return result
                DurableChatRepository.read_events = expire_result
            policy = None
            tenant = None
            synthetic_source = ''
            if args.auth_management:
                from app.auth_http import AuthPolicy
                policy = AuthPolicy("http://127.0.0.1:8019", secure_cookie=False)
                tenant = repositories.identity.create_tenant("Synthetic browser tenant")
                if args.durable_knowledge:
                    source = settings.KNOWLEDGE_DIR/tenant/'synthetic-input'
                    source.mkdir(parents=True)
                    (source/'synthetic.txt').write_text('Synthetic browser knowledge evidence for durable import Worker.',encoding='utf-8')
                    synthetic_source = str(source)
                repositories.identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
                # Deterministic models for request-bound gateway instances too.
                # No test endpoint may accidentally construct a real HTTP client.
                from app.providers import ModelGateway
                def synthetic_snapshot(gateway):
                    return ProviderSnapshot("deepseek", "isolated-function-model", "unused", gateway.repository.credential_namespace())
                def synthetic_build(gateway, selected):
                    if selected.tenant_id != gateway.repository.credential_namespace():
                        raise ValueError("Synthetic model namespace mismatch")
                    return FunctionModel(stream_function=model_stream)
                def no_real_client(gateway):
                    raise RuntimeError("Real model HTTP is disabled in this synthetic harness")
                ModelGateway.snapshot = synthetic_snapshot
                ModelGateway.build_model = synthetic_build
                ModelGateway._client = no_real_client
            app = create_app(repositories=repositories, secret_store=SecretStore(path=settings.MASTER_KEY_PATH),
                             knowledge_paths={"knowledge_dir": settings.KNOWLEDGE_DIR, "asset_dir": settings.KNOWLEDGE_ASSET_DIR,
                                              "legacy_upload_dir": settings.LEGACY_UPLOAD_DIR}, auth_policy=policy,
                             durable_chat=args.durable_chat,durable_knowledge=args.durable_knowledge,storage_policy=storage_policy)
            if args.auth_management:
                from fastapi.responses import HTMLResponse
                @app.get("/__acceptance/auth", response_class=HTMLResponse, include_in_schema=False)
                def auth_acceptance():
                    # Synthetic fixtures only. Not a production login UI.
                    return '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>隔离认证验收</title>
                    <h1>隔离认证验收（合成账号）</h1><p>仅供 Cookie/管理权限测试，不是生产登录页。</p>
                    <p>后台导入样本路径：KNOWLEDGE_SOURCE_FIXTURE</p>
                    <button onclick="run('login')">登录合成管理员</button>
                    <button onclick="run('setup')">初始化本租户</button>
                    <button onclick="run('me')">刷新身份</button>
                    <button onclick="run('providers')">读取本租户配置</button>
                    <button onclick="run('logout')">退出</button>
                    <button onclick="run('knowledge-upload')">上传合成知识</button>
                    <button onclick="run('knowledge-list')">读取本租户文档</button>
                    <button onclick="run('knowledge-search')">检索合成知识</button>
                    <button onclick="run('knowledge-reparse')">重解析合成文档</button>
                    <button onclick="run('chat-create')">新建合成会话</button>
                    <button onclick="run('chat-send')">流式合成回答</button>
                    <button onclick="run('chat-history')">读取会话历史</button><pre id="result">未登录</pre>
                    <script>let csrf='',docId='',sessionId=''; async function run(action){
                    let path='/api/auth/'+action,options={};
                    if(action==='login') options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'synthetic.admin',password:'synthetic-password-only',tenant_id:'TENANT_FIXTURE'})};
                    if(action==='setup'){path='/api/admin/setup';options={method:'POST',headers:{'X-CSRF-Token':csrf}};}
                    if(action==='providers') path='/api/admin/providers';
                    if(action==='logout') options={method:'POST',headers:{'X-CSRF-Token':csrf}};
                    if(action==='knowledge-upload'){path='/api/admin/documents';const body=new FormData();body.append('file',new Blob(['Synthetic lexical evidence for browser acceptance.'],{type:'text/plain'}),'synthetic.txt');options={method:'POST',headers:{'X-CSRF-Token':csrf},body};}
                    if(action==='knowledge-list')path='/api/admin/documents';
                    if(action==='knowledge-search'){path='/api/admin/knowledge/search-test';options={method:'POST',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/json'},body:JSON.stringify({query:'lexical'})};}
                    if(action==='knowledge-reparse'){path='/api/admin/documents/'+docId+'/reparse';options={method:'POST',headers:{'X-CSRF-Token':csrf}};}
                    if(action==='chat-create'){path='/api/chat/sessions';options={method:'POST',headers:{'X-CSRF-Token':csrf}};}
                    if(action==='chat-send'){path='/api/chat/sessions/'+sessionId+'/messages';options={method:'POST',headers:{'X-CSRF-Token':csrf,'Content-Type':'application/json'},body:JSON.stringify({content:'合成客服验收，请回答。'})};}
                    if(action==='chat-history')path='/api/chat/sessions/'+sessionId;
                    const r=await fetch(path,options);
                    if(action==='chat-send'&&r.ok){const reader=r.body.getReader(),decoder=new TextDecoder();let pending='',answer='',done=false;
                    while(true){const chunk=await reader.read();if(chunk.done)break;pending+=decoder.decode(chunk.value,{stream:true});let end;
                    while((end=pending.indexOf('\\n\\n'))>=0){const frame=pending.slice(0,end);pending=pending.slice(end+2);const lines=frame.split('\\n');
                    const kind=lines.find(x=>x.startsWith('event: '))?.slice(7),raw=lines.find(x=>x.startsWith('data: '))?.slice(6);if(!raw)continue;const data=JSON.parse(raw);
                    if(kind==='token')answer+=data.text;if(kind==='answer_reset')answer='';if(kind==='done')done=true;
                    document.getElementById('result').textContent=JSON.stringify({action,status:r.status,answer,done,error:kind==='error'?data.code:undefined});}}return;}
                    const value=await r.json(); if(value.csrf_token)csrf=value.csrf_token;
                    if(action==='chat-create'&&value.id)sessionId=value.id;
                    if(action==='knowledge-upload'&&value.id)docId=value.id;
                    document.getElementById('result').textContent=JSON.stringify({action,status:r.status,role:value.principal?.role,
                    tenant:value.principal?.tenant_id,providerCount:value.providers?.providers?.length??value.providers?.length,
                    documentCount:Array.isArray(value)?value.length:undefined,hitCount:value.items?.length,version:value.version,messageCount:value.messages?.length,
                    loggedOut:value.logged_out,detail:value.detail});}</script></html>'''.replace('TENANT_FIXTURE', tenant).replace('KNOWLEDGE_SOURCE_FIXTURE',synthetic_source)
                # The application SPA catch-all was registered by create_app;
                # this exact harness-only GET route must precede it.
                app.router.routes.insert(0, app.router.routes.pop())
            app.state.chat.gateway = Gateway()
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8019, log_level="warning"))
            if args.recovery_gate_acceptance:
                from fastapi.responses import JSONResponse
                from app.persistence.schema import metadata
                import sqlalchemy as sa
                assert repositories.engine.url.database.startswith('cs_browser_')

                @app.middleware('http')
                async def synthetic_recovery_control(request, call_next):
                    # Harness-only: production has NO hold/release/stop HTTP API.
                    if request.method == 'POST' and request.url.path == '/__acceptance/maintenance':
                        from app.maintenance import Maintenance
                        Maintenance(repositories.engine).request(seconds=60)
                        return JSONResponse({'maintenance':True})
                    if request.method == 'POST' and request.url.path == '/__acceptance/hold':
                        with repositories.engine.begin() as db:
                            db.execute(metadata.tables['recovery_state'].insert().values(id=1,blocked=True,
                                backup_id=uuid.uuid4(),restored_at=sa.func.clock_timestamp()))
                        return JSONResponse({'blocked':True})
                    if request.method == 'POST' and request.url.path == '/__acceptance/shutdown':
                        server.should_exit = True
                        return JSONResponse({'stopping':True})
                    return await call_next(request)

            @app.post("/__acceptance/shutdown", include_in_schema=False)
            async def shutdown():
                # Exists only in this isolated harness; localhost middleware applies.
                server.should_exit = True
                return {"stopping": True}

            worker = None
            import_worker = None
            try:
                if args.durable_chat:
                    worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name('chat_worker.py')),
                        '--database-url', repositories.engine.url.render_as_string(hide_password=False),
                        '--master-key-path', str(settings.MASTER_KEY_PATH),
                        '--knowledge-root', str(settings.KNOWLEDGE_DIR), '--asset-root', str(settings.KNOWLEDGE_ASSET_DIR),
                        '--legacy-root', str(settings.LEGACY_UPLOAD_DIR), '--synthetic', '--synthetic-delay', str(args.synthetic_delay)],
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if args.durable_knowledge and not args.knowledge_queue_only:
                    import_worker = subprocess.Popen([sys.executable,str(Path(__file__).with_name('knowledge_worker.py')),
                        '--database-url',repositories.engine.url.render_as_string(hide_password=False),
                        '--knowledge-root',str(settings.KNOWLEDGE_DIR),'--asset-root',str(settings.KNOWLEDGE_ASSET_DIR),
                        '--legacy-root',str(settings.LEGACY_UPLOAD_DIR)],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                server.run()
            finally:
                # End the exact owned child before dropping its disposable DB.
                for child in (worker,import_worker):
                    if child is not None:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=10)


if __name__ == "__main__":
    main()
