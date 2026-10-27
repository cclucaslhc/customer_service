"""Fixed-version encrypted catalog fetch; no source DB or service bootstrap."""
import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup_catalog_archive import fetch_checkpoint
from app.backup_storage import operator_store


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--anchor',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--namespace',type=uuid.UUID,required=True)
    p.add_argument('--schedule-id',type=uuid.UUID,required=True)
    p.add_argument('--policy-fingerprint',required=True)
    p.add_argument('--key-path',type=Path,required=True)
    p.add_argument('--scratch-root',type=Path,required=True)
    p.add_argument('--storage-policy',type=Path,required=True)
    p.add_argument('--storage-target',required=True)
    p.add_argument('--storage-credentials-stdin',action='store_true')
    p.add_argument('--confirm-isolated-catalog-fetch',action='store_true')
    args=p.parse_args(); store=None
    try:
        if not args.storage_credentials_stdin or not args.confirm_isolated_catalog_fetch:
            raise ValueError('Explicit isolated catalog fetch and independent credentials required')
        store=operator_store(policy_path=args.storage_policy,target_name=args.storage_target,credential_stream=sys.stdin)
        result=fetch_checkpoint(store=store,namespace=str(args.namespace),anchor_path=args.anchor,
            destination=args.destination,key_path=args.key_path,scratch_root=args.scratch_root,
            expected_schedule_id=args.schedule_id,expected_policy_fingerprint=args.policy_fingerprint)
        print(json.dumps(result)); return 0
    except Exception:
        print('Catalog fetch failed; retain destination isolated and review exact anchor/version/policy. No database, Worker or model was enabled.',file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__=='__main__':
    raise SystemExit(main())
