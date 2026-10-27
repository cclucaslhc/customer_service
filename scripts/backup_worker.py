"""Explicit logical backup scheduler; no auto configuration or credentials."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup_scheduler import BackupCatalog,BackupPlan,BackupRunner
from app.backup import _validated_url
from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.backup_storage import operator_stores
import sqlalchemy as sa


def load_plan(path):
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError('Explicit bounded backup plan required')
    value = json.loads(path.read_text(encoding='utf-8'))
    for name in ('binary_dir','knowledge_root','asset_root','package_root','key_path','scratch_root'):
        value[name] = Path(value[name])
    value['schedule_id'] = uuid.UUID(value['schedule_id'])
    plan = BackupPlan(**value)
    plan.validate()
    return plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--configure',action='store_true')
    p.add_argument('--once',action='store_true')
    p.add_argument('--confirm-global-backup',action='store_true')
    p.add_argument('--verification-root',type=Path)
    p.add_argument('--confirm-isolated-verification',action='store_true')
    p.add_argument('--storage-policy',type=Path)
    p.add_argument('--source-target')
    p.add_argument('--archive-target')
    p.add_argument('--catalog-target')
    p.add_argument('--verification-object-target')
    p.add_argument('--confirm-isolated-object-verification',action='store_true')
    p.add_argument('--storage-credentials-stdin',action='store_true')
    args = p.parse_args()
    engine = None
    stores = {}
    try:
        if not args.confirm_global_backup or (args.configure and args.once):
            raise ValueError('Explicit global backup authorization and one operation required')
        url = os.environ.get('BACKUP_DATABASE_URL')
        if not url:
            raise ValueError('Explicit backup database required')
        _validated_url(url)
        plan = load_plan(args.plan)
        object_verification = args.verification_object_target is not None
        if object_verification != args.confirm_isolated_object_verification:
            raise ValueError('Object verification target and explicit isolation confirmation required together')
        if object_verification and (plan.restore_policy is None or not args.confirm_isolated_verification
                or args.verification_root is None or not os.environ.get('BACKUP_VERIFICATION_ADMIN_URL')):
            raise ValueError('Complete isolated restore authorization required before object credentials')
        targets = {role:target for role,target in (('source',args.source_target),('archive',args.archive_target),
                   ('verification',args.verification_object_target),('catalog',args.catalog_target))
                   if target is not None}
        if bool(targets.get('source')) != (plan.object_policy is not None) or bool(targets.get('archive')) != (plan.archive_policy is not None):
            raise ValueError('Storage roles differ from approved plan')
        if bool(targets.get('catalog')) != (plan.catalog_policy is not None):
            raise ValueError('Catalog role differs from approved plan')
        configured = (args.storage_policy is not None,bool(targets),args.storage_credentials_stdin)
        if any(configured):
            if not all(configured):
                raise ValueError('Policy, targets and explicit credential envelope required together')
            stores = operator_stores(policy_path=args.storage_policy,targets=targets,credential_stream=sys.stdin)
            for role,expected in (('source',plan.object_policy),('archive',plan.archive_policy),('catalog',plan.catalog_policy)):
                actual = stores[role].policy_fingerprint if role in stores else None
                if actual != expected:
                    raise ValueError('Storage destination differs from approved plan')
            for role,store in stores.items():
                if role != 'verification':
                    store.check_ready()
        engine = build_engine(url,pool_size=4)
        with engine.connect() as db:
            if db.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() != SCHEMA_REVISION:
                raise ValueError('Backup scheduler schema mismatch')
        verifier = None
        verification_url = os.environ.get('BACKUP_VERIFICATION_ADMIN_URL')
        if verification_url or args.verification_root is not None or args.confirm_isolated_verification:
            if not verification_url or args.verification_root is None or not args.confirm_isolated_verification:
                raise ValueError('Explicit isolated verification URL/root/authorization required together')
            from app.backup_verifier import LocalRestoreVerifier,ObjectRestoreTargets
            object_targets = ObjectRestoreTargets(stores['verification'],isolated_object_environment_confirmed=True) if object_verification else None
            verifier = LocalRestoreVerifier(admin_url=verification_url,root=args.verification_root,
                isolated_environment_confirmed=True,object_targets=object_targets)
        runner = BackupRunner(engine,url,plan,verifier=verifier,object_store=stores.get('source'),
            archive_store=stores.get('archive'),archive_namespace=plan.archive_namespace,catalog_store=stores.get('catalog'))
        if args.configure:
            BackupCatalog(engine).configure(plan)
            print(json.dumps({'configured':True,'schedule_id':str(plan.schedule_id),'fingerprint':plan.fingerprint}))
            return 0
        if args.once:
            print(json.dumps(runner.run_once() or {'claimed':False}))
            return 0
        stop = threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        while not stop.is_set():
            report = runner.run_once()
            if report:
                print(json.dumps(report),flush=True)
            stop.wait(5)
        return 0
    except Exception:
        print('Backup Worker failed; do not replay unknown attempts or automatically clear maintenance. Review catalog and owned artifacts.',file=sys.stderr)
        return 1
    finally:
        for store in stores.values():
            try:
                store.close()
            except Exception:
                pass
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
