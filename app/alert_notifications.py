"""Explicit outbound alert adapters; no automatic retries or credential discovery.

Only registered alert names and firing/resolved leave the host. Provider acceptance
does not prove a human received/read the notification. Callers must authenticate
Alertmanager before invoking these adapters; this module exposes no public route.
"""
import base64
from dataclasses import dataclass,field
from email.message import EmailMessage
from email.headerregistry import Address
import hashlib
import hmac
import smtplib
import ssl
import time
from urllib.parse import urlsplit,parse_qs
import httpx

ALERT_NAMES=frozenset('''CustomerServiceLocalInferenceCollectionUnavailable
CustomerServiceLocalInferenceUnresolved CustomerServiceBackupCollectionUnavailable
CustomerServiceBackupVerificationStale CustomerServiceBackupNeedsReview
CustomerServiceBackupProtectionUnhealthy CustomerServiceModelCircuitCollectionUnavailable
CustomerServiceModelCircuitOpened CustomerServiceModelCircuitProbeStuck
CustomerServiceHttpLogPipelineUnavailable CustomerServiceHttpLogLoss
CustomerServiceHttpLogBacklog CustomerServiceModelBudgetCollectionUnavailable
CustomerServiceModelBudgetSoftThreshold CustomerServiceModelBudgetRejected
CustomerServiceModelBudgetUnresolvedEvidence'''.split())


class NotificationError(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


@dataclass(frozen=True)
class AlertNotice:
    alert: str
    status: str

    def text(self):
        if self.alert not in ALERT_NAMES or self.status not in {'firing','resolved'}:
            raise ValueError('Registered alert and firing/resolved status required')
        return f'Customer service alert\n{self.alert}\nStatus: {self.status}'


def _webhook(url,channel):
    parts=urlsplit(url)
    host,path,key=({'wecom':('qyapi.weixin.qq.com','/cgi-bin/webhook/send','key'),
        'dingtalk':('oapi.dingtalk.com','/robot/send','access_token')})[channel]
    query=parse_qs(parts.query,keep_blank_values=True)
    if (parts.scheme!='https' or parts.netloc!=host or parts.path!=path or parts.fragment
        or set(query)!={key} or len(query[key])!=1 or not query[key][0]):
        raise ValueError('Explicit official HTTPS robot endpoint required')


def _post(url,payload,*,params=None,transport=None):
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=10,transport=transport) as client:
            target=httpx.URL(url).copy_merge_params(params) if params else url
            with client.stream('POST',target,json=payload) as response:
                if response.is_redirect: raise NotificationError('notification_redirect_rejected')
                if response.status_code!=200: raise NotificationError('notification_provider_rejected')
                raw=bytearray()
                for block in response.iter_bytes(chunk_size=4096):
                    raw.extend(block)
                    if len(raw)>16384: raise NotificationError('notification_invalid_response')
            import json
            result=json.loads(raw)
            if not isinstance(result,dict) or type(result.get('errcode')) is not int or result['errcode']!=0:
                raise NotificationError('notification_provider_rejected')
    except NotificationError: raise
    except Exception:
        # The request may already have reached the provider. Do not auto-retry.
        raise NotificationError('notification_delivery_unknown') from None
    return 'provider_accepted'


def send_wecom(notice,webhook_url,*,transport=None):
    _webhook(webhook_url,'wecom')
    return _post(webhook_url,{'msgtype':'text','text':{'content':notice.text()}},transport=transport)


def send_dingtalk(notice,webhook_url,*,signing_secret,transport=None):
    _webhook(webhook_url,'dingtalk')
    if not isinstance(signing_secret,str) or not signing_secret:
        raise ValueError('Explicit DingTalk signing secret required')
    stamp=str(int(time.time()*1000))
    signature=base64.b64encode(hmac.new(signing_secret.encode(),
        (stamp+'\n'+signing_secret).encode(),hashlib.sha256).digest()).decode()
    return _post(webhook_url,{'msgtype':'text','text':{'content':notice.text()},
        'at':{'isAtAll':False}},params={'timestamp':stamp,'sign':signature},transport=transport)


@dataclass(frozen=True)
class EmailReceiver:
    host: str
    username: str=field(repr=False)
    password: str=field(repr=False)
    sender: str=field(repr=False)
    recipient: str=field(repr=False)
    port: int=465


def send_email(notice,receiver,*,smtp_factory=smtplib.SMTP_SSL):
    text=notice.text()
    for value in (receiver.host,receiver.username,receiver.sender,receiver.recipient):
        if not isinstance(value,str) or not value or '\r' in value or '\n' in value:
            raise ValueError('Explicit single-line email configuration required')
    if receiver.port!=465: raise ValueError('SMTP over TLS port 465 required')
    for value in (receiver.sender,receiver.recipient):
        address=Address(addr_spec=value)
        if not address.username or not address.domain: raise ValueError('Single mailbox required')
    message=EmailMessage()
    message['From']=receiver.sender
    message['To']=receiver.recipient
    message['Subject']='Customer service '+notice.status+': '+notice.alert
    message.set_content(text)
    try:
        with smtp_factory(receiver.host,receiver.port,timeout=10,context=ssl.create_default_context()) as client:
            client.login(receiver.username,receiver.password)
            refused=client.send_message(message)
            if refused: raise NotificationError('notification_provider_rejected')
    except NotificationError: raise
    except Exception:
        raise NotificationError('notification_delivery_unknown') from None
    return 'provider_accepted'
