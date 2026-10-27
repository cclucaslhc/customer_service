"""Explicit owner rollout actions from a private receipt, never auto-approval."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_rollout_service import RagRolloutService
from app.runtime_files import worker_database_url
from scripts.rag_rollout_watch import load_control

BASE=('tenant_id','rollout_id','expected_rollout_version','expected_plan_sha256')
ACTIONS=('observe','start','advance','pause','withdraw','prepare-reversion')


def dispatch(controller,action,control,*,confirmed=False,new_index_id=None):
    if action not in ACTIONS:
        raise ValueError('Unknown rollout action')
    if action!='observe' and confirmed is not True:
        raise ValueError('Explicit change confirmation required')
    if (action=='prepare-reversion')!=(new_index_id is not None):
        raise ValueError('A fresh index ID is required only for prepare-reversion')
    args={key:control[key] for key in BASE}
    if action in ('start','advance'):
        args.update({key:control[key] for key in
            ('expected_release_version','expected_body_sha256','current_binding')})
    if action=='prepare-reversion':
        args.update({key:control[key] for key in ('expected_release_version','expected_body_sha256')})
        args['new_index_id']=new_index_id
    method={'withdraw':'rollback','prepare-reversion':'prepare_reversion'}.get(action,action)
    return getattr(controller,method)(**args)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database-url',help='Loopback test URL only')
    source.add_argument('--database-url-file',type=Path)
    parser.add_argument('--control-file',type=Path,required=True)
    parser.add_argument('--confirm-change',action='store_true')
    parser.add_argument('--new-index-id')
    parser.add_argument('action',choices=ACTIONS)
    args=parser.parse_args(argv)
    if args.action!='observe' and not args.confirm_change:
        parser.error('Mutation requires --confirm-change')
    if (args.action=='prepare-reversion')!=(args.new_index_id is not None):
        parser.error('--new-index-id is required only for prepare-reversion')
    control=load_control(args.control_file)
    engine=build_engine(worker_database_url(args.database_url,args.database_url_file),pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()!=SCHEMA_REVISION:
                raise RuntimeError('Rollout control requires exact schema head')
        result=dispatch(RagRolloutService(engine),args.action,control,
            confirmed=args.confirm_change,new_index_id=args.new_index_id)
        print(json.dumps(result,sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print('RAG rollout control failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
