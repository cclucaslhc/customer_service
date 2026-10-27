"""Owner-only, bounded cleanup of vectors for non-current document versions."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_index_admin import RagIndexAdmin
from app.runtime_files import worker_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument('--database-url', help='Loopback test URL only; visible in process arguments')
    database.add_argument('--database-url-file', type=Path, help='Absolute private migration-owner URL file')
    parser.add_argument('--tenant-id', required=True)
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--confirm-stale-prune', action='store_true',
                        help='Confirm one bounded batch after backup/freshness review')
    args = parser.parse_args()
    if not args.confirm_stale_prune:
        parser.error('Explicit --confirm-stale-prune is required')
    url = worker_database_url(args.database_url, args.database_url_file)
    engine = build_engine(url, pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() != SCHEMA_REVISION:
                raise RuntimeError('RAG pruning requires exact schema head')
        removed = RagIndexAdmin(engine).prune_stale_embeddings(
            tenant_id=args.tenant_id, batch_size=args.batch_size)
        print(f'Stale RAG embeddings removed: {removed}')
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('RAG pruning failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
