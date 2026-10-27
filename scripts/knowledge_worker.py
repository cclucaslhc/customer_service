"""Explicit PostgreSQL import Worker using shared trusted roots, no model/key access."""
import argparse
from pathlib import Path
import signal
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.knowledge_worker import KnowledgeWorker
from app.persistence.bundle import PostgreSQLRepositories
from app.runtime_files import worker_database_url


def main():
    p = argparse.ArgumentParser(description=__doc__)
    database = p.add_mutually_exclusive_group(required=True)
    database.add_argument('--database-url',help='Local/test compatibility only; exposes URL in process arguments')
    database.add_argument('--database-url-file',type=Path,help='Existing private single-line PostgreSQL URL file')
    for name in ('knowledge-root','asset-root','legacy-root'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--once',action='store_true')
    p.add_argument('--parser-timeout-seconds',type=float,default=120)
    p.add_argument('--parser-memory-mib',type=int,default=2048)
    p.add_argument('--storage-policy',type=Path,help='Explicit non-secret approved target JSON file')
    p.add_argument('--storage-master-key-path',type=Path,help='Explicit shared encryption key file, never a service credential')
    p.add_argument('--require-app-role',action='store_true',help='Enforce runtime role on loopback or tunneled PostgreSQL too')
    p.add_argument('--otlp-traces-endpoint',help='Explicit approved OTLP/HTTP /v1/traces endpoint; no implicit telemetry credentials')
    args = p.parse_args()
    paths = {key:Path(value).resolve() for key,value in (
        ('knowledge_dir',args.knowledge_root),('asset_dir',args.asset_root),('legacy_upload_dir',args.legacy_root))}
    if any(not Path(value).is_absolute() for value in (args.knowledge_root,args.asset_root,args.legacy_root)):
        raise ValueError('Explicit absolute shared roots are required')
    database_url = worker_database_url(args.database_url,args.database_url_file)
    bundle = PostgreSQLRepositories.from_url(database_url,pool_size=3,
        strict_role=True if args.require_app_role else None,runtime_role='worker')
    tracing = None
    try:
        bundle.check_schema()
        if args.otlp_traces_endpoint is not None:
            from app.otlp_export import PrivateOtlpTracing
            tracing = PrivateOtlpTracing(args.otlp_traces_endpoint)
        storage = None
        if (args.storage_policy is None) != (args.storage_master_key_path is None):
            raise ValueError('Storage policy and master key path must be explicit together')
        if args.storage_policy is not None:
            from app.storage_config import StoragePolicy, StorageConfiguration
            from app.security import SecretStore
            if not args.storage_policy.is_absolute() or not args.storage_master_key_path.is_absolute():
                raise ValueError('Storage policy and key paths must be absolute')
            storage = StorageConfiguration(bundle.engine,SecretStore(path=args.storage_master_key_path),StoragePolicy.from_file(args.storage_policy))
        from app.isolated_parser import IsolatedDocumentParser
        document_parser = IsolatedDocumentParser(timeout_seconds=args.parser_timeout_seconds,
            memory_mib=args.parser_memory_mib)
        # Refuse startup before claiming real jobs if this node cannot enforce
        # the parser policy or load dependencies within its resource limits.
        document_parser('startup-probe.txt',b'Synthetic parser startup capability check.')
        worker = KnowledgeWorker(bundle,paths,storage_configuration=storage,tracing=tracing,
            document_parser=document_parser)
        if args.once:
            worker.run_once()
            return
        stop = threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        worker.run(stop)
    finally:
        bundle.close()
        if tracing:
            tracing.flush(2)
            tracing.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('Knowledge Worker failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
