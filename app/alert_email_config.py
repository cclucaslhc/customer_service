"""Encrypted operator email configuration, separate from tenant/model settings."""
import base64
from dataclasses import asdict
from email.headerregistry import Address
import json
import os
from pathlib import Path
import re
import stat
from app.alert_notifications import EmailReceiver


def gmail_receiver(account,password,recipient):
    for value in (account,password,recipient):
        if not isinstance(value,str) or not value or '\r' in value or '\n' in value:
            raise ValueError('Invalid Gmail configuration')
    for value in (account,recipient):
        address=Address(addr_spec=value)
        if not address.username or not address.domain or len(value)>254:
            raise ValueError('Single mailbox required')
    if not account.lower().endswith('@gmail.com'):
        raise ValueError('Gmail account required')
    password=password.replace(' ','')
    if not re.fullmatch('[A-Za-z0-9]{16}',password):
        raise ValueError('Gmail application password required')
    return EmailReceiver('smtp.gmail.com',account,password,account,recipient)


class EncryptedEmailConfig:
    def __init__(self,path,secrets):
        self.path=Path(path)
        self.secrets=secrets
        if not self.path.is_absolute() or self.path.is_symlink() or not self.path.parent.is_dir():
            raise ValueError('Explicit existing operator directory required')

    def save_new(self,receiver):
        # No replacement or migration of any pre-existing credential file.
        if self.path.exists() or self.path.is_symlink(): raise FileExistsError('Already configured')
        ciphertext,nonce=self.secrets.encrypt(json.dumps(asdict(receiver)),'alert-email-v1')
        envelope=json.dumps({'format':'customer-service-alert-email-v1',
            'ciphertext':base64.b64encode(ciphertext).decode(),'nonce':base64.b64encode(nonce).decode()}).encode()
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(fd,'wb') as output:
                output.write(envelope)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            # Keep a possible partial file for explicit operator recovery; never
            # silently replace/erase an existing or concurrently created file.
            raise

    def read(self):
        fd=os.open(self.path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>16384:
                raise ValueError('Invalid encrypted email file')
            if os.name=='posix' and stat.S_IMODE(info.st_mode)&0o077:
                raise ValueError('Email configuration must be private')
            raw=source.read(16385)
        if len(raw)>16384: raise ValueError('Email configuration too large')
        envelope=json.loads(raw)
        if set(envelope)!={'format','ciphertext','nonce'} or envelope['format']!='customer-service-alert-email-v1':
            raise ValueError('Unknown email configuration')
        value=json.loads(self.secrets.decrypt(base64.b64decode(envelope['ciphertext'],validate=True),
            base64.b64decode(envelope['nonce'],validate=True),'alert-email-v1'))
        receiver=gmail_receiver(value['username'],value['password'],value['recipient'])
        if asdict(receiver)!=value: raise ValueError('Invalid encrypted Gmail policy')
        return receiver
