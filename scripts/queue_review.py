"""Read-only terminal queue inventory; use only an approved backup/read role."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.persistence.engine import build_engine
from app.queue_review import list_queue_failures
from app.runtime_files import read_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file',type=Path,required=True,
                        help='Existing private file containing a read-only PostgreSQL URL')
    parser.add_argument('--limit',type=int,default=100)
    args = parser.parse_args()
    engine = build_engine(read_database_url(args.database_url_file),pool_size=1)
    try:
        print(json.dumps({'tasks':list_queue_failures(engine,limit=args.limit)},ensure_ascii=False,separators=(',',':')))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Queue review failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
