"""Private single-instance operator notification receiver, never a public API."""
import argparse
import logging
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from app.alert_email_config import EncryptedEmailConfig
    from app.alert_notifications import send_email
    from app.alert_receiver import create_alert_receiver
    from app.runtime_files import read_private_line
    from app.security import SecretStore
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-path',required=True,type=Path)
    parser.add_argument('--bearer-token-file',required=True,type=Path)
    parser.add_argument('--create-bearer-token',action='store_true')
    parser.add_argument('--port',type=int,default=8012)
    args=parser.parse_args()
    if not 1024<=args.port<=65535: raise ValueError('Unprivileged loopback port required')
    if args.create_bearer_token:
        if not args.bearer_token_file.is_absolute() or not args.bearer_token_file.parent.is_dir():
            raise ValueError('Explicit operator token file required')
        descriptor=os.open(args.bearer_token_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'w') as output: output.write(secrets.token_hex(32))
    token=read_private_line(args.bearer_token_file,maximum=128).decode('ascii')
    store=EncryptedEmailConfig(args.config_path,SecretStore())
    logging.disable(logging.CRITICAL)
    uvicorn.run(create_alert_receiver(token,lambda notice:send_email(notice,store.read())),
        host='127.0.0.1',port=args.port,access_log=False,log_level='critical',
        proxy_headers=False,limit_concurrency=8,timeout_keep_alive=5)


if __name__=='__main__': main()
