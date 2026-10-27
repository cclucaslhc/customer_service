"""Owner-operated release review actions; administrator token only via hidden TTY prompt."""
import argparse
import getpass
import json
from pathlib import Path
import sys
import warnings

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_release_service import RagReleaseService
from app.runtime_files import read_private_line,worker_database_url

FIELDS={'tenant_id','release_id','expected_version','expected_body_sha256','current_binding'}


def load_receipt(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('Duplicate receipt field')
            result[key]=value
        return result
    result=json.loads(read_private_line(path,maximum=65536),object_pairs_hook=unique)
    if not isinstance(result,dict) or set(result)!=FIELDS or not isinstance(result['current_binding'],dict):
        raise ValueError('Exact release receipt required; credentials must not be in receipt')
    return result


def dispatch(service,action,receipt,*,confirmed=False,prompt=None):
    if action not in ('approve','reject','check','execute'):
        raise ValueError('Unknown release action')
    if action!='check' and confirmed is not True:
        raise ValueError('Explicit change confirmation required')
    if action in ('approve','reject'):
        if prompt is None:
            if not sys.stdin.isatty():
                raise ValueError('Administrator review requires an interactive terminal')
            prompt=getpass.getpass
        with warnings.catch_warnings():
            # getpass otherwise falls back to echoed stdin on some terminals.
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=prompt('Current designated administrator session token (hidden): ')
        if not isinstance(token,str) or not token or len(token)>4096:
            raise ValueError('Invalid explicit administrator credential')
        try:
            return getattr(service,action)(**receipt,token=token)
        finally:
            token=None
    return getattr(service,'check_ready' if action=='check' else 'execute')(**receipt)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database-url',help='Loopback test URL only')
    source.add_argument('--database-url-file',type=Path)
    parser.add_argument('--receipt-file',type=Path,required=True)
    parser.add_argument('--confirm-change',action='store_true')
    parser.add_argument('action',choices=('approve','reject','check','execute'))
    args=parser.parse_args(argv)
    if args.action!='check' and not args.confirm_change:
        parser.error('Mutation requires --confirm-change')
    receipt=load_receipt(args.receipt_file)
    engine=build_engine(worker_database_url(args.database_url,args.database_url_file),pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()!=SCHEMA_REVISION:
                raise RuntimeError('Release control requires exact schema head')
        print(json.dumps(dispatch(RagReleaseService(engine),args.action,receipt,
            confirmed=args.confirm_change),sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print('RAG release control failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
