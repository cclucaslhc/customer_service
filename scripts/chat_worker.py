"""Explicit standalone Worker entry point; never discover credentials or migrate."""
import argparse
import asyncio
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.chat_worker import ChatWorker
from app.persistence.bundle import PostgreSQLRepositories
from app.security import SecretStore


def parser():
    result = argparse.ArgumentParser(description='Consume authenticated PostgreSQL chat commands')
    database = result.add_mutually_exclusive_group(required=True)
    database.add_argument('--database-url',help='Local/test compatibility only; exposes URL in process arguments')
    database.add_argument('--database-url-file',type=Path,help='Existing private single-line PostgreSQL URL file')
    result.add_argument('--master-key-path',required=True)
    result.add_argument('--knowledge-root',required=True)
    result.add_argument('--asset-root',required=True)
    result.add_argument('--legacy-root',required=True)
    result.add_argument('--once',action='store_true')
    result.add_argument('--synthetic',action='store_true',help='Disposable local cs_test_/cs_browser_ databases only; no real model client')
    result.add_argument('--synthetic-delay',type=float,default=0.05)
    result.add_argument('--synthetic-trace-output',type=Path,help='New owned temporary JSONL evidence file; synthetic mode only')
    result.add_argument('--otlp-traces-endpoint',help='Explicit approved OTLP/HTTP /v1/traces endpoint; no implicit telemetry credentials')
    redis_source = result.add_mutually_exclusive_group()
    redis_source.add_argument('--redis-url',help='Local credential-free compatibility only')
    redis_source.add_argument('--redis-url-file',type=Path,help='Existing private single-line Redis URL file')
    result.add_argument('--admission-namespace')
    result.add_argument('--global-capacity',type=int)
    result.add_argument('--starts-per-minute',type=int)
    result.add_argument('--storage-policy',type=Path)
    result.add_argument('--storage-master-key-path',type=Path)
    result.add_argument('--require-app-role',action='store_true',help='Enforce runtime role on loopback or tunneled PostgreSQL too')
    result.add_argument('--local-model-base-url',help='This host only: http://127.0.0.1:<port>/v1')
    result.add_argument('--rag-embedding-dir', type=Path, help='Verified offline local Embedding directory')
    result.add_argument('--rag-reranker-dir', type=Path, help='Verified offline local Reranker directory')
    result.add_argument('--rag-device', choices=('cpu','cuda'), default='cpu')
    result.add_argument('--rag-max-length', type=int, default=1024)
    result.add_argument('--enable-rag-experimental', action='store_true',
                        help='Explicitly enable pre-blind-test hybrid retrieval; not a production gate')
    return result


async def run(args):
    from app.runtime_files import worker_database_url, worker_redis_url
    from app.local_model_endpoint import LocalModelEndpoint
    local_model_base_url = getattr(args, 'local_model_base_url', None)
    local_endpoint = LocalModelEndpoint(local_model_base_url) if local_model_base_url else None
    rag_embedding = rag_reranker = None
    if args.enable_rag_experimental:
        if args.rag_embedding_dir is None or args.rag_reranker_dir is None:
            raise ValueError('Experimental RAG requires both local model directories')
        for directory in (args.rag_embedding_dir, args.rag_reranker_dir):
            if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
                raise ValueError('Explicit absolute non-symlink RAG model directory required')
        from app.rag_local import LocalEmbeddingGateway, LocalReranker
        rag_embedding = LocalEmbeddingGateway(args.rag_embedding_dir,
            device=args.rag_device, max_length=args.rag_max_length)
        rag_reranker = LocalReranker(args.rag_reranker_dir,
            device=args.rag_device, max_length=args.rag_max_length)
    elif args.rag_embedding_dir is not None or args.rag_reranker_dir is not None:
        raise ValueError('RAG model directories require explicit experimental enablement')
    database_url = worker_database_url(args.database_url,args.database_url_file)
    redis_url = worker_redis_url(args.redis_url,args.redis_url_file)
    factory = None
    admission = None
    tracing = None
    if args.synthetic_trace_output is not None and not args.synthetic:
        raise ValueError('Trace evidence output requires isolated synthetic mode')
    if args.synthetic_trace_output is not None and args.otlp_traces_endpoint is not None:
        raise ValueError('Select one trace export destination')
    settings = (redis_url,args.admission_namespace,args.global_capacity,args.starts_per_minute)
    if any(value is not None for value in settings):
        if any(value is None for value in settings):
            raise ValueError('All shared admission settings must be explicit')
        from app.admission import RedisAdmission, AdmissionPolicy
        admission = RedisAdmission.from_url(redis_url,AdmissionPolicy(args.admission_namespace,args.global_capacity,args.starts_per_minute))
    if args.synthetic:
        from sqlalchemy.engine import make_url
        selected = make_url(database_url)
        if selected.host not in {'localhost','127.0.0.1','::1'} or not (selected.database or '').startswith(('cs_test_','cs_browser_')):
            raise ValueError('Synthetic Worker requires a disposable local database')
        temp = Path(tempfile.gettempdir()).resolve()
        for name in ('master_key_path','knowledge_root','asset_root','legacy_root'):
            path = Path(getattr(args,name)).resolve()
            if path == temp or temp not in path.parents:
                raise ValueError('Synthetic Worker requires owned temporary paths')
        if not 0 <= args.synthetic_delay <= 120:
            raise ValueError('Invalid synthetic delay')
        from pydantic_ai.models.function import FunctionModel
        from app.providers import ProviderSnapshot
        async def synthetic(messages,info):
            yield '这是独立 Worker 的隔离回答。'
            await asyncio.sleep(args.synthetic_delay)
            yield '最终消息和事件已持久化。'
        class Gateway:
            def __init__(self,job):
                self.job = job
            def snapshot(self):
                return ProviderSnapshot(self.job['provider'],self.job['model'],'unused-synthetic-only')
            def build_model(self,snapshot):
                return FunctionModel(stream_function=synthetic)
        factory = lambda credential,job:Gateway(job)
        if args.synthetic_trace_output is not None:
            output = args.synthetic_trace_output.resolve()
            if not args.synthetic_trace_output.is_absolute() or temp not in output.parents:
                raise ValueError('Synthetic trace output requires an explicit temporary path')
            with output.open('x',encoding='utf-8'):
                pass
            import json
            from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
            from app.tracing import SafeTracing
            class EvidenceExporter(SpanExporter):
                def export(self, spans):
                    with output.open('a',encoding='utf-8') as target:
                        for span in spans:
                            target.write(json.dumps({'name':span.name,'trace_id':f'{span.context.trace_id:032x}',
                                'span_id':f'{span.context.span_id:016x}',
                                'parent_id':f'{span.parent.span_id:016x}' if span.parent else None,
                                'attributes':dict(span.attributes or {}),'status':span.status.status_code.name})+'\n')
                    return SpanExportResult.SUCCESS
            tracing = SafeTracing(EvidenceExporter())
    repositories = PostgreSQLRepositories.from_url(database_url,pool_size=3,
        strict_role=True if args.require_app_role else None,runtime_role='worker')
    try:
        repositories.check_schema()
        if args.otlp_traces_endpoint is not None:
            from app.otlp_export import PrivateOtlpTracing
            tracing = PrivateOtlpTracing(args.otlp_traces_endpoint)
        storage = None
        if (args.storage_policy is None) != (args.storage_master_key_path is None):
            raise ValueError('Storage policy and master key path must be explicit together')
        if args.storage_policy is not None:
            from app.storage_config import StoragePolicy, StorageConfiguration
            if not args.storage_master_key_path.is_absolute():
                raise ValueError('Explicit absolute storage master key path required')
            storage = StorageConfiguration(repositories.engine,SecretStore(path=args.storage_master_key_path),StoragePolicy.from_file(args.storage_policy))
        worker = ChatWorker(repositories,SecretStore(path=Path(args.master_key_path)),
            {'knowledge_dir':Path(args.knowledge_root),'asset_dir':Path(args.asset_root),'legacy_upload_dir':Path(args.legacy_root)},
            gateway_factory=factory,admission=admission,storage_configuration=storage,tracing=tracing,
            local_model_endpoint=local_endpoint,rag_embedding_gateway=rag_embedding,
            rag_reranker=rag_reranker)
        if args.once:
            await worker.run_once()
            return
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT,signal.SIGTERM):
            try:
                loop.add_signal_handler(name,stop.set)
            except NotImplementedError:
                signal.signal(name,lambda *_:loop.call_soon_threadsafe(stop.set))
        await worker.run(stop)
    finally:
        repositories.close()
        if admission:
            admission.close()
        if tracing:
            await asyncio.to_thread(tracing.flush,2)
            await asyncio.to_thread(tracing.close)


if __name__ == '__main__':
    try:
        asyncio.run(run(parser().parse_args()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('Worker failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
