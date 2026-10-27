"""Explicit bounded event retention. No default policy, model/key access or migrations."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.durable_chat_repository import DurableChatRepository, validate_retention


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url',required=True)
    parser.add_argument('--retention-seconds',required=True,type=int)
    parser.add_argument('--limit',type=int,default=100)
    args = parser.parse_args()
    validate_retention(args.retention_seconds)
    if not 1 <= args.limit <= 100:
        raise ValueError('Limit must be between 1 and 100')
    bundle = PostgreSQLRepositories.from_url(args.database_url,pool_size=1)
    try:
        bundle.check_schema()
        result = DurableChatRepository(bundle.engine).prune_events(retention_seconds=args.retention_seconds,limit=args.limit)
        print('Pruned Runs: '+str(result['runs'])+'; events: '+str(result['events']))
    finally:
        bundle.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Event maintenance failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
