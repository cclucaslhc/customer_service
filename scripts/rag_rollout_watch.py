"""Explicit owner-only health monitoring; never advances or resumes a rollout."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_rollout_service import RagRolloutService
from app.runtime_files import read_private_line,worker_database_url

FIELDS=frozenset(('tenant_id','rollout_id','expected_rollout_version','expected_plan_sha256',
    'expected_release_version','expected_body_sha256','current_binding'))


def load_control(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('Duplicate control field')
            result[key]=value
        return result
    control=json.loads(read_private_line(path,maximum=65536),object_pairs_hook=unique)
    if not isinstance(control,dict) or set(control)!=FIELDS or not isinstance(control['current_binding'],dict):
        raise ValueError('Explicit health control receipt and trusted current binding required')
    return control


def run_checks(controller,control_file,*,checks,interval_seconds,sleep=time.sleep,emit=print):
    if type(checks) is not int or not 1<=checks<=1000000:
        raise ValueError('Explicit bounded check count required')
    if type(interval_seconds) is not int or not 1<=interval_seconds<=60:
        raise ValueError('Check interval must be 1-60 seconds')
    for index in range(checks):
        # Reread the operator-maintained runtime binding, not the approved
        # binding from the release table. Never silently refresh stale receipts.
        result=controller.check_health_and_pause(**load_control(control_file))
        decision=result['health']['decision']
        emit(json.dumps({'decision':decision,'state':result['state'],'version':result['version'],
            'reason_codes':result['health']['reason_codes']},sort_keys=True))
        if decision=='pause':
            return 2
        if index+1<checks:
            sleep(interval_seconds)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database-url',help='Loopback test URL only')
    source.add_argument('--database-url-file',type=Path)
    parser.add_argument('--control-file',required=True,type=Path,
        help='Private absolute single-line JSON receipt with trusted CURRENT runtime binding')
    parser.add_argument('--checks',type=int,default=1,help='Explicitly opt into repeated checks; default once')
    parser.add_argument('--interval-seconds',type=int,default=30)
    args=parser.parse_args(argv)
    # Validate before opening a database connection.
    if not 1<=args.checks<=1000000 or not 1<=args.interval_seconds<=60:
        parser.error('checks must be 1-1000000 and interval-seconds 1-60')
    load_control(args.control_file)
    engine=build_engine(worker_database_url(args.database_url,args.database_url_file),pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()!=SCHEMA_REVISION:
                raise RuntimeError('Health monitoring requires exact schema head')
        return run_checks(RagRolloutService(engine),args.control_file,checks=args.checks,
            interval_seconds=args.interval_seconds)
    finally:
        engine.dispose()


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        # No URLs, operator file contents, SQL parameters or credentials in logs.
        print('RAG health monitor failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
