"""Explicit loopback API entry point for a same-host HTTPS reverse proxy.

This assembles authenticated PostgreSQL and optional private telemetry. It does
not migrate, provision accounts, discover credentials, or expose a public port.
"""

import argparse
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime_files import read_database_url, read_private_line, require_private_file


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--database-url-file', type=Path, required=True,
                        help='Existing private file containing one PostgreSQL API-role URL')
    result.add_argument('--master-key-path', type=Path, required=True)
    result.add_argument('--origin', required=True, help='Canonical external HTTPS origin')
    for name in ('knowledge-root', 'asset-root', 'legacy-root'):
        result.add_argument('--' + name, type=Path, required=True)
    result.add_argument('--storage-policy', type=Path, required=True,
                        help='Approved private object target list; no service credentials')
    result.add_argument('--port', type=int, default=8000)
    result.add_argument('--metrics-token-file', type=Path)
    result.add_argument('--metrics-port', type=int)
    result.add_argument('--otlp-traces-endpoint')
    result.add_argument('--json-http-logs', action='store_true')
    result.add_argument('--local-model-base-url', help='This host only: http://127.0.0.1:<port>/v1')
    result.add_argument('--rag-embedding-dir', type=Path, help='Verified offline local Embedding directory')
    result.add_argument('--rag-reranker-dir', type=Path, help='Verified offline local Reranker directory')
    result.add_argument('--rag-device', choices=('cpu','cuda'), default='cpu')
    result.add_argument('--rag-max-length', type=int, default=1024)
    result.add_argument('--enable-rag-experimental', action='store_true',
                        help='Explicitly enable pre-blind-test hybrid retrieval; not a production gate')
    result.add_argument('--event-retention-seconds', type=int)
    return result


def validate(args):
    from app.auth_http import AuthPolicy
    from app.local_model_endpoint import LocalModelEndpoint
    policy = AuthPolicy(args.origin)
    if not policy.secure_cookie or urlsplit(args.origin).scheme != 'https':
        raise ValueError('Only an external HTTPS origin is allowed')
    if type(args.port) is not int or not 1 <= args.port <= 65535:
        raise ValueError('Invalid loopback API port')
    for path in (args.knowledge_root, args.asset_root, args.legacy_root):
        if not path.is_absolute():
            raise ValueError('Explicit absolute data roots are required')
    if (args.metrics_token_file is None) != (args.metrics_port is None):
        raise ValueError('Metrics token and port must be supplied together')
    if args.metrics_port is not None and (type(args.metrics_port) is not int or not 1 <= args.metrics_port <= 65535
                                            or args.metrics_port == args.port):
        raise ValueError('Invalid private metrics port')
    require_private_file(args.database_url_file, maximum=4096)
    require_private_file(args.master_key_path, maximum=32, exact_size=32)
    require_private_file(args.storage_policy, maximum=16384)
    if args.metrics_token_file is not None:
        require_private_file(args.metrics_token_file, maximum=512)
    if args.local_model_base_url is not None:
        LocalModelEndpoint(args.local_model_base_url)
    rag_enabled = getattr(args, 'enable_rag_experimental', False)
    embedding_dir = getattr(args, 'rag_embedding_dir', None)
    reranker_dir = getattr(args, 'rag_reranker_dir', None)
    if rag_enabled:
        if embedding_dir is None or reranker_dir is None:
            raise ValueError('Experimental RAG requires both local model directories')
        for directory in (embedding_dir, reranker_dir):
            if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
                raise ValueError('Explicit absolute non-symlink RAG model directory required')
    elif embedding_dir is not None or reranker_dir is not None:
        raise ValueError('RAG model directories require explicit experimental enablement')
    return policy


def build_runtime(args):
    """Return owned resources; the caller must close them after server exit."""
    from app.http_observability import HttpTelemetry
    from app.log_export import BoundedJsonLogSink
    from app.main import create_app
    from app.persistence.bundle import PostgreSQLRepositories
    from app.private_metrics import MetricsPolicy
    from app.security import SecretStore
    from app.storage_config import StoragePolicy
    from app.local_model_endpoint import LocalModelEndpoint

    auth = validate(args)
    database_url = read_database_url(args.database_url_file)
    repositories = PostgreSQLRepositories.from_url(database_url, pool_size=5, strict_role=True, runtime_role='api')
    tracing = None
    log_sink = None
    try:
        rag_embedding = rag_reranker = None
        if getattr(args, 'enable_rag_experimental', False):
            from app.rag_local import LocalEmbeddingGateway, LocalReranker
            rag_embedding = LocalEmbeddingGateway(args.rag_embedding_dir,
                device=args.rag_device, max_length=args.rag_max_length)
            rag_reranker = LocalReranker(args.rag_reranker_dir,
                device=args.rag_device, max_length=args.rag_max_length)
        storage_policy = StoragePolicy.from_file(args.storage_policy)
        metrics = None
        if args.metrics_token_file is not None:
            token = read_private_line(args.metrics_token_file, maximum=512)
            metrics = MetricsPolicy(token=token, port=args.metrics_port)
        if args.otlp_traces_endpoint is not None:
            from app.otlp_export import PrivateOtlpTracing
            tracing = PrivateOtlpTracing(args.otlp_traces_endpoint)
        if args.json_http_logs:
            def write_line(line):
                sys.stdout.write(line)
                sys.stdout.flush()
            log_sink = BoundedJsonLogSink(write_line)
        telemetry = HttpTelemetry(sink=log_sink) if log_sink else None
        app = create_app(repositories=repositories, secret_store=SecretStore(path=args.master_key_path),
                         allow_authenticated_proxy_clients=True,
                         knowledge_paths={'knowledge_dir': args.knowledge_root, 'asset_dir': args.asset_root,
                                          'legacy_upload_dir': args.legacy_root},
                         auth_policy=auth, durable_chat=True, durable_knowledge=True,
                         storage_policy=storage_policy, http_telemetry=telemetry,
                         metrics_policy=metrics, tracing=tracing,
                         rag_embedding_gateway=rag_embedding, rag_reranker=rag_reranker,
                         local_model_endpoint=LocalModelEndpoint(args.local_model_base_url) if args.local_model_base_url else None,
                         event_retention_seconds=args.event_retention_seconds)
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(args.origin).hostname])
        return app, repositories, tracing, log_sink
    except BaseException:
        repositories.close()
        if tracing is not None:
            tracing.flush(2)
            tracing.close()
        if log_sink is not None:
            log_sink.close()
        raise


def main():
    import uvicorn
    args = parser().parse_args()
    app, repositories, tracing, log_sink = build_runtime(args)
    try:
        # Only the local proxy can connect or set X-Forwarded-Proto. Never use
        # forwarded_allow_ips='*', which would let a client claim HTTPS.
        config = uvicorn.Config(app, host='127.0.0.1', port=args.port, proxy_headers=True,
                                forwarded_allow_ips='127.0.0.1', access_log=False,
                                log_config=None, log_level='warning')
        uvicorn.Server(config).run()
    finally:
        repositories.close()
        if tracing is not None:
            tracing.flush(2)
            tracing.close()
        if log_sink is not None:
            log_sink.flush(2)
            log_sink.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print('API startup failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
