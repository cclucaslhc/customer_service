"""Offline encrypted logical backup/isolated restore. No implicit credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backup import create_backup, restore_backup
from app.backup_storage import operator_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('create', 'restore'))
    parser.add_argument('--binary-dir', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--knowledge-root', type=Path, required=True)
    parser.add_argument('--asset-root', type=Path, required=True)
    parser.add_argument('--key-path', type=Path, required=True)
    parser.add_argument('--scratch-root', type=Path, required=True)
    parser.add_argument('--confirm-offline-maintenance', action='store_true')
    parser.add_argument('--confirm-isolated-empty-restore', action='store_true')
    parser.add_argument('--storage-policy', type=Path)
    parser.add_argument('--storage-target')
    parser.add_argument('--storage-credentials-stdin', action='store_true',
                        help='Read independent backup/restore credentials as bounded JSON from stdin')
    parser.add_argument('--maintenance-owner',type=uuid.UUID)
    parser.add_argument('--maintenance-fence',type=int)
    args = parser.parse_args()
    url = os.environ.get('BACKUP_DATABASE_URL')
    if not url:
        parser.error('BACKUP_DATABASE_URL must be supplied explicitly')
    common = dict(url=url, binary_dir=args.binary_dir, knowledge_root=args.knowledge_root,
                  asset_root=args.asset_root, key_path=args.key_path, scratch_root=args.scratch_root)
    store = None
    maintenance_engine = None
    try:
        permit_fields = (args.maintenance_owner is not None,args.maintenance_fence is not None)
        if any(permit_fields):
            if not all(permit_fields) or args.operation != 'create' or not args.confirm_offline_maintenance:
                raise ValueError('A complete maintenance permit is supported only for authorized backup creation')
            from app.backup import _validated_url
            from app.persistence.engine import build_engine
            from app.maintenance import Maintenance,MaintenancePermit
            _validated_url(url)
            maintenance_engine = build_engine(url,pool_size=1)
            window = Maintenance(maintenance_engine).sealed(MaintenancePermit(args.maintenance_owner,args.maintenance_fence))
        else:
            window = nullcontext(None)
        configured = (args.storage_policy is not None,args.storage_target is not None,args.storage_credentials_stdin)
        if any(configured):
            if not all(configured):
                raise ValueError('Storage policy, approved target and explicit stdin credentials are required together')
            if not (args.confirm_offline_maintenance if args.operation == 'create' else args.confirm_isolated_empty_restore):
                raise ValueError('Explicit maintenance/restore authorization required before credential input')
            store = operator_store(policy_path=args.storage_policy,target_name=args.storage_target,credential_stream=sys.stdin)
            store.check_ready()
            common['object_store'] = store
        if args.operation == 'create':
            with window as guard:
                report = create_backup(**common, destination=args.package,
                                       maintenance_confirmed=args.confirm_offline_maintenance,maintenance_guard=guard)
        else:
            report = restore_backup(**common, source=args.package,
                                    isolated_confirmed=args.confirm_isolated_empty_restore)
        print(json.dumps(report))
    except Exception:
        print('Logical backup/restore failed; keep destination isolated. Check authorization, empty target, '
              'offline maintenance, explicit paths and archive integrity. No Workers were enabled.', file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
        if maintenance_engine is not None:
            maintenance_engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
