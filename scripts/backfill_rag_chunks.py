"""Backfill current chunk snapshots after 0027, using the migration DB role."""

import argparse
import os

from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_backfill import backfill_current_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size', type=int, default=100)
    args = parser.parse_args()
    url = os.environ.get('MIGRATION_DATABASE_URL')
    if not url:
        parser.error('MIGRATION_DATABASE_URL is required; no default database is used')
    engine = build_engine(url, pool_size=1)
    try:
        with engine.connect() as db:
            head = db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()
            if head != SCHEMA_REVISION:
                raise RuntimeError(f'RAG backfill requires exact schema head {SCHEMA_REVISION}')
        count = backfill_current_chunks(engine, batch_size=args.batch_size)
        print(f'Current-version chunk snapshots inserted: {count}')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
