"""Build missing current-version local Embeddings for one explicit tenant/profile."""

import argparse
import math
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.persistence.bundle import PostgreSQLRepositories
from app.rag_indexer import RagIndexWorker
from app.rag_local import LocalEmbeddingGateway
from app.runtime_files import worker_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument('--database-url', help='Loopback test database only; URL appears in process arguments')
    database.add_argument('--database-url-file', type=Path, help='Absolute private PostgreSQL URL file')
    parser.add_argument('--tenant-id', required=True, help='Explicit tenant UUID')
    parser.add_argument('--profile-id', required=True, help='Explicit pinned Embedding profile UUID')
    parser.add_argument('--model-dir', required=True, type=Path, help='Absolute verified local Embedding model directory')
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--max-length', type=int, default=1024,
                        help='Embedding tokenizer limit; must match profile max_tokens')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--max-batches', type=int, default=100)
    parser.add_argument('--poll-seconds', type=float, default=0,
                        help='0 processes one bounded pass; positive value keeps indexing new documents')
    parser.add_argument('--require-worker-role', action='store_true',
                        help='Enforce least-privilege Worker role on loopback too')
    args = parser.parse_args()
    if not args.model_dir.is_absolute() or args.model_dir.is_symlink() or not args.model_dir.is_dir():
        raise ValueError('An absolute non-symlink local model directory is required')
    if not 1 <= args.max_batches <= 100000:
        raise ValueError('Invalid maximum batch count')
    if not math.isfinite(args.poll_seconds) or args.poll_seconds < 0 or args.poll_seconds > 3600:
        raise ValueError('Invalid polling interval')
    url = worker_database_url(args.database_url, args.database_url_file)
    bundle = PostgreSQLRepositories.from_url(url, pool_size=1, runtime_role='worker',
        strict_role=True if args.require_worker_role else None)
    try:
        bundle.check_schema()
        gateway = LocalEmbeddingGateway(args.model_dir, device=args.device,
                                        max_length=args.max_length)
        worker = RagIndexWorker(bundle.engine, tenant_id=args.tenant_id,
            profile_id=args.profile_id, embedding_gateway=gateway)
        stop = threading.Event()
        for event in (signal.SIGINT, signal.SIGTERM):
            signal.signal(event, lambda *_: stop.set())
        while not stop.is_set():
            total = 0
            for _ in range(args.max_batches):
                if stop.is_set():
                    break
                count = worker.run_batch(batch_size=args.batch_size)
                if count == 0:
                    break
                total += count
            print(f'Current-version Embeddings inserted: {total}', flush=True)
            if args.poll_seconds == 0 or stop.wait(args.poll_seconds):
                break
    finally:
        bundle.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('RAG index Worker failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
