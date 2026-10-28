"""No real recipients, credentials or outbound traffic."""
import json
from pathlib import Path
import httpx
import pytest
import yaml
from app.alert_notifications import (ALERT_NAMES,AlertNotice,EmailReceiver,NotificationError,
    send_email,send_wecom,send_dingtalk)

NOTICE=AlertNotice('CustomerServiceModelBudgetRejected','firing')
WECOM='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=synthetic-test-only'
DING='https://oapi.dingtalk.com/robot/send?access_token=synthetic-test-only'


def test_registered_names_match_all_shipped_rules():
    found=set()
    for path in (Path(__file__).resolve().parents[1]/'ops/prometheus').glob('*alerts.yml'):
        for group in yaml.safe_load(path.read_text())['groups']:
            found.update(rule['alert'] for rule in group['rules'] if 'alert' in rule)
    assert found==ALERT_NAMES


def test_three_adapter_payloads_and_dingtalk_token_signature_are_preserved():
    requests=[]
    def accept(request):
        requests.append(request)
        return httpx.Response(200,json={'errcode':0})
    transport=httpx.MockTransport(accept)
    assert send_wecom(NOTICE,WECOM,transport=transport)=='provider_accepted'
    assert send_dingtalk(NOTICE,DING,signing_secret='synthetic-signature-only',transport=transport)=='provider_accepted'
    assert requests[1].url.params['access_token']=='synthetic-test-only'
    assert requests[1].url.params['sign'] and requests[1].url.params['timestamp']
    assert json.loads(requests[1].content)['at']=={'isAtAll':False}
    for request in requests:
        assert json.loads(request.content)['text']['content']==NOTICE.text()


@pytest.mark.parametrize('url',('http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x',
    'https://evil.invalid/cgi-bin/webhook/send?key=x',WECOM+'&extra=x',
    'https://qyapi.weixin.qq.com.evil.invalid/cgi-bin/webhook/send?key=x'))
def test_unapproved_robot_endpoint_rejected_before_transport(url):
    with pytest.raises(ValueError): send_wecom(NOTICE,url)


@pytest.mark.parametrize('response,code',((httpx.Response(302,headers={'Location':'https://evil.invalid'}),'notification_redirect_rejected'),
    (httpx.Response(200,json={'errcode':400,'errmsg':'synthetic-secret'}),'notification_provider_rejected'),
    (httpx.Response(200,content=b'x'*20000),'notification_invalid_response')))
def test_robot_failure_is_safe_and_not_retried(response,code):
    attempts=[]
    def handle(request): attempts.append(True); return response
    with pytest.raises(NotificationError) as error:
        send_wecom(NOTICE,WECOM,transport=httpx.MockTransport(handle))
    assert str(error.value)==code and len(attempts)==1


def test_transport_failure_remains_unknown_without_url_or_key():
    def fail(request): raise httpx.ReadTimeout('synthetic-secret-key-url')
    with pytest.raises(NotificationError) as error:
        send_wecom(NOTICE,WECOM,transport=httpx.MockTransport(fail))
    assert str(error.value)=='notification_delivery_unknown'


def test_email_requires_tls_and_sends_only_fixed_alert_fields():
    sent=[]
    class SMTP:
        def __init__(self,host,port,timeout,context):
            assert port==465 and context.check_hostname
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def login(self,user,password): assert password=='synthetic-test-only'
        def send_message(self,message): sent.append(message); return {}
    receiver=EmailReceiver('smtp.example.invalid','synthetic-user','synthetic-test-only',
        'sender@example.invalid','receiver@example.invalid')
    assert 'synthetic-test-only' not in repr(receiver)
    assert send_email(NOTICE,receiver,smtp_factory=SMTP)=='provider_accepted'
    assert sent[0].get_content().strip()==NOTICE.text()
    with pytest.raises(ValueError):
        send_email(AlertNotice('unregistered','firing'),receiver,smtp_factory=SMTP)
