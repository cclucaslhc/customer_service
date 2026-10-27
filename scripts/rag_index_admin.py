"""Migration-owner RAG index stage, completeness seal and atomic activation."""

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
    parser.add_argument('--index-id', required=True)
    actions = parser.add_subparsers(dest='action', required=True)
    stage = actions.add_parser('stage')
    stage.add_argument('--profile-id', required=True)
    stage.add_argument('--max-tokens', required=True, type=int)
    stage.add_argument('--lexical-version', required=True)
    stage.add_argument('--chunker-version', required=True)
    stage.add_argument('--reranker-version')
    existing = actions.add_parser('stage-existing')
    existing.add_argument('--profile-id', required=True)
    existing.add_argument('--lexical-version', required=True)
    existing.add_argument('--chunker-version', required=True)
    existing.add_argument('--reranker-version')
    actions.add_parser('seal')
    activate = actions.add_parser('activate')
    expectation = activate.add_mutually_exclusive_group(required=True)
    expectation.add_argument('--expected-active-index-id',
                             help='Observed active index UUID before this tenant rollout')
    expectation.add_argument('--expect-no-active-index', action='store_true',
                             help='First activation for this tenant')
    args = parser.parse_args()
    url = worker_database_url(args.database_url, args.database_url_file)
    engine = build_engine(url, pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() != SCHEMA_REVISION:
                raise RuntimeError('RAG index administration requires exact schema head')
        admin = RagIndexAdmin(engine)
        if args.action == 'stage':
            admin.stage(tenant_id=args.tenant_id, index_id=args.index_id,
                profile_id=args.profile_id, max_tokens=args.max_tokens,
                lexical_version=args.lexical_version, chunker_version=args.chunker_version,
                reranker_version=args.reranker_version)
            print('RAG index staged')
        elif args.action == 'stage-existing':
            admin.stage_existing(tenant_id=args.tenant_id, index_id=args.index_id,
                profile_id=args.profile_id, lexical_version=args.lexical_version,
                chunker_version=args.chunker_version,
                reranker_version=args.reranker_version)
            print('RAG index staged from existing profile')
        elif args.action == 'seal':
            report = admin.seal(tenant_id=args.tenant_id, index_id=args.index_id)
            print(f"RAG index sealed: {report['current_chunks']} current chunks, manifest {report['manifest_sha256']}")
        else:
            report = admin.activate(tenant_id=args.tenant_id, index_id=args.index_id,
                expected_active_index_id=args.expected_active_index_id)
            print(f"RAG index activated: {report['current_chunks']} current chunks, manifest {report['manifest_sha256']}")
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('RAG index administration failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
