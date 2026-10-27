"""Owner-only drained capacity changes and confirmed abandoned-parser release."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.knowledge_admission import KnowledgeAdmissionAdmin
from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.runtime_files import worker_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument('--database-url')
    database.add_argument('--database-url-file', type=Path)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('inspect')
    configure = actions.add_parser('configure')
    configure.add_argument('--capacity', type=int, required=True)
    configure.add_argument('--tenant-capacity', type=int, required=True)
    release = actions.add_parser('release-abandoned')
    release.add_argument('--job-id', required=True)
    release.add_argument('--owner', required=True)
    release.add_argument('--fence', type=int, required=True)
    release.add_argument('--confirm-parser-stopped', action='store_true')
    args = parser.parse_args()
    engine = build_engine(worker_database_url(args.database_url, args.database_url_file), pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() != SCHEMA_REVISION:
                raise RuntimeError('Exact migration head required')
        admin = KnowledgeAdmissionAdmin(engine)
        if args.action == 'inspect':
            print(json.dumps(admin.snapshot(), sort_keys=True))
        elif args.action == 'configure':
            admin.configure(capacity=args.capacity, tenant_capacity=args.tenant_capacity)
            print('Shared knowledge capacity configured')
        else:
            changed = admin.release_abandoned(job_id=args.job_id, owner=args.owner, fence=args.fence,
                parser_stopped_confirmed=args.confirm_parser_stopped)
            print('Abandoned knowledge reservation released' if changed else 'Reservation already released')
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Knowledge capacity administration failed: '+type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
