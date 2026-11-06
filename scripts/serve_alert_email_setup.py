"""Explicit local-only credential form; never expose through a public proxy."""
import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from app.alert_email_config import EncryptedEmailConfig
    from app.alert_email_setup import create_setup_app
    from app.security import SecretStore
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-path',required=True,type=Path)
    parser.add_argument('--port',default=8011,type=int)
    args=parser.parse_args()
    store=EncryptedEmailConfig(args.config_path,SecretStore())
    logging.disable(logging.CRITICAL)
    uvicorn.run(create_setup_app(store,args.port),host='127.0.0.1',port=args.port,
        access_log=False,log_level='critical',proxy_headers=False,limit_concurrency=8,
        timeout_keep_alive=5)


if __name__=='__main__': main()
