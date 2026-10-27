"""Explicit drained PostgreSQL policy provisioning; no model/secret discovery."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.admission import AdmissionPolicy
from app.persistence.admission_repository import configure_policy
from app.persistence.bundle import PostgreSQLRepositories


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database-url',required=True)
    p.add_argument('--namespace',required=True)
    p.add_argument('--capacity',required=True,type=int)
    p.add_argument('--starts-per-minute',required=True,type=int)
    args = p.parse_args()
    policy = AdmissionPolicy(args.namespace,args.capacity,args.starts_per_minute)
    bundle = PostgreSQLRepositories.from_url(args.database_url,pool_size=1)
    try:
        bundle.check_schema()
        configure_policy(bundle.engine,policy)
        print('Shared admission policy configured: '+policy.fingerprint)
    finally:
        bundle.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Admission configuration failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
