"""Explicit read-only catalog health/Prometheus output. No repair or notifications."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup import _validated_url
from app.backup_health import BackupHealth,prometheus,publish_metrics
from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
import sqlalchemy as sa


def emit_metrics(report=None):
    # Prometheus text exposition requires LF, including on Windows pipes.
    sys.stdout.buffer.write(prometheus(report).encode('utf-8'))
    sys.stdout.buffer.flush()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--schedule-id',type=uuid.UUID,required=True)
    p.add_argument('--policy-fingerprint',required=True)
    p.add_argument('--max-age-seconds',type=int,required=True)
    p.add_argument('--require-remote',action='store_true')
    p.add_argument('--local-only',action='store_true',help='Explicitly waive actual restore requirement, not remote requirement')
    p.add_argument('--format',choices=('json','prometheus'),default='json')
    p.add_argument('--textfile',type=Path,help='Atomic dedicated customer-service-backup.prom publication')
    p.add_argument('--confirm-global-catalog-read',action='store_true')
    args = p.parse_args()
    engine = None
    try:
        if not args.confirm_global_catalog_read:
            raise ValueError('Explicit global catalog read authorization required')
        if args.textfile is not None and args.format != 'prometheus':
            raise ValueError('Textfile publication requires Prometheus format')
        url = os.environ.get('BACKUP_DATABASE_URL')
        if not url:
            raise ValueError('Explicit backup database required')
        _validated_url(url)
        engine = build_engine(url,pool_size=1)
        with engine.connect() as db:
            if db.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() != SCHEMA_REVISION:
                raise ValueError('Backup monitor schema mismatch')
        report = BackupHealth(engine).inspect(schedule_id=args.schedule_id,policy_fingerprint=args.policy_fingerprint,
            max_age_seconds=args.max_age_seconds,require_remote=args.require_remote,require_restore=not args.local_only)
        if args.format == 'prometheus':
            if args.textfile is not None:
                publish_metrics(args.textfile,report)
            emit_metrics(report)
        else:
            print(json.dumps(report))
        return 0 if report['healthy'] else 2
    except Exception:
        if args.format == 'prometheus':
            if args.textfile is not None and args.confirm_global_catalog_read:
                try:
                    publish_metrics(args.textfile)
                except Exception:
                    pass  # Existing file expiry is detected by collection timestamp alarm.
            emit_metrics()
        else:
            print(json.dumps({'collection_success':False,'healthy':False}))
        print('Backup catalog collection failed; no repair performed. Check explicit read authorization, schema and monitoring policy.',file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
