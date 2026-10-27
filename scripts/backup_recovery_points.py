"""Approved anchor-set selection/optional isolated fetch; no source DB or replay."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup_storage import operator_store
from app.backup_recovery_points import select_recovery_point
from app.backup_catalog_archive import fetch_checkpoint


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True)
    p.add_argument('--namespace',type=uuid.UUID,required=True)
    p.add_argument('--schedule-id',type=uuid.UUID,required=True)
    p.add_argument('--policy-fingerprint',required=True)
    p.add_argument('--not-after',type=datetime.fromisoformat,required=True)
    p.add_argument('--min-snapshot',type=datetime.fromisoformat)
    p.add_argument('--max-gap-seconds',type=int,required=True)
    p.add_argument('--allow-unrestored',action='store_true')
    p.add_argument('--destination',type=Path)
    p.add_argument('--key-path',type=Path,required=True)
    p.add_argument('--scratch-root',type=Path,required=True)
    p.add_argument('--storage-policy',type=Path,required=True)
    p.add_argument('--storage-target',required=True)
    p.add_argument('--storage-credentials-stdin',action='store_true')
    p.add_argument('--confirm-isolated-recovery-selection',action='store_true')
    args=p.parse_args(); store=None
    try:
        if not args.storage_credentials_stdin or not args.confirm_isolated_recovery_selection:
            raise ValueError('Explicit recovery review and independent credentials required')
        store=operator_store(policy_path=args.storage_policy,target_name=args.storage_target,credential_stream=sys.stdin)
        result=select_recovery_point(store=store,namespace=str(args.namespace),manifest_path=args.manifest,
            expected_manifest_sha256=args.manifest_sha256,key_path=args.key_path,expected_schedule_id=args.schedule_id,
            expected_policy_fingerprint=args.policy_fingerprint,not_after=args.not_after,min_snapshot=args.min_snapshot,
            max_gap_seconds=args.max_gap_seconds,require_restore=not args.allow_unrestored)
        if args.destination is not None:
            selected=result['selected']
            result['fetch']=fetch_checkpoint(store=store,namespace=str(args.namespace),anchor_path=Path(selected['anchor_path']),
                expected_anchor_sha256=selected['anchor_sha256'],destination=args.destination,key_path=args.key_path,
                scratch_root=args.scratch_root,expected_schedule_id=args.schedule_id,expected_policy_fingerprint=args.policy_fingerprint)
        print(json.dumps(result)); return 0
    except Exception:
        print('Recovery point selection failed; review approved inventory/time/verification. No fallback, replay or service activation occurred.',file=sys.stderr)
        return 1
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


if __name__=='__main__':
    raise SystemExit(main())
