"""Explicit encrypted remote archive/fetch; no database, model or credential discovery."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup_archive import archive_package, fetch_package
from app.backup_storage import operator_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('archive','fetch'))
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--key-path',type=Path,required=True)
    parser.add_argument('--scratch-root',type=Path,required=True)
    parser.add_argument('--namespace',required=True)
    parser.add_argument('--storage-policy',type=Path,required=True)
    parser.add_argument('--storage-target',required=True)
    parser.add_argument('--storage-credentials-stdin',action='store_true')
    args = parser.parse_args()
    store = None
    try:
        if not args.storage_credentials_stdin:
            raise ValueError('Explicit independent archive credential input required')
        store = operator_store(policy_path=args.storage_policy,target_name=args.storage_target,credential_stream=sys.stdin)
        common = {'receipt':args.receipt,'store':store,'namespace':args.namespace,
                  'key_path':args.key_path,'scratch_root':args.scratch_root}
        report = archive_package(package=args.package,**common) if args.operation == 'archive' else fetch_package(destination=args.package,**common)
        print(json.dumps(report))
        return 0
    except Exception:
        print('Remote backup archive/fetch failed; no package was enabled. Keep remote versions for reconciliation.',file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
