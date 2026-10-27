"""Trusted maintenance control; no application startup or implicit credentials."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup import _validated_url
from app.persistence.engine import build_engine
from app.maintenance import Maintenance,MaintenancePermit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=('request','renew','release'))
    p.add_argument('--owner',type=uuid.UUID)
    p.add_argument('--fence',type=int)
    p.add_argument('--lease-seconds',type=int,default=60)
    p.add_argument('--takeover-expired',action='store_true')
    p.add_argument('--confirm-global-maintenance',action='store_true')
    args = p.parse_args()
    engine = None
    try:
        if not args.confirm_global_maintenance:
            raise ValueError('Explicit global operator authorization required')
        url = os.environ.get('MAINTENANCE_DATABASE_URL')
        if not url:
            raise ValueError('Explicit maintenance database required')
        _validated_url(url)
        if args.operation == 'request' and (args.owner is not None or args.fence is not None):
            raise ValueError('Request creates new ownership')
        if args.operation != 'request' and (args.owner is None or args.fence is None or args.takeover_expired):
            raise ValueError('Explicit current permit required')
        engine = build_engine(url,pool_size=1)
        engine_owner = Maintenance(engine)
        if args.operation == 'request':
            permit = engine_owner.request(seconds=args.lease_seconds,takeover_expired=args.takeover_expired)
        else:
            permit = MaintenancePermit(args.owner,args.fence)
            if args.operation == 'renew':
                engine_owner.renew(permit,seconds=args.lease_seconds)
            else:
                engine_owner.release(permit)
        print(json.dumps({'operation':args.operation,'owner':str(permit.owner),'fence':permit.fence}))
        return 0
    except Exception:
        print('Maintenance operation failed; existing hold is not automatically cleared. Check authorized ownership and live executions.',file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
