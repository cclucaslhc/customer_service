"""Isolated snapshot validation/import CLI, never uses live application data."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.persistence.engine import build_engine
from app.persistence.legacy_import import import_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        if not args.dry_run:
            url = os.environ.get("MIGRATION_DATABASE_URL")
            if not url:
                parser.error("MIGRATION_DATABASE_URL is required for import")
            engine = build_engine(url, pool_size=1)
        print(json.dumps(import_snapshot(args.source, engine, dry_run=args.dry_run), indent=2))
    except Exception:
        # Database errors can contain SQL parameters, phone/email and connection URLs.
        print("Migration failed; destination transaction rolled back. Check the isolated source schema, typed values and destination constraints.", file=sys.stderr)
        return 1
    finally:
        if engine:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
