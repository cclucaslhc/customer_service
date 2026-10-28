"""Private receiver contracts; all delivery callbacks are synthetic."""
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.alert_receiver import create_alert_receiver
from app.alert_notifications import NotificationError
import httpx
import pytest

TOKEN='syntheticreceiveronly01234567890123456789'
HEADERS={'Authorization':'Bearer '+TOKEN}
NAME='CustomerServiceModelBudgetRejected'


def client(sender):
    return TestClient(create_alert_receiver(TOKEN,sender),base_url='http://127.0.0.1',client=('127.0.0.1',1234))


def body(status='firing'):
    return {'alerts':[{'status':status,'labels':{'alertname':NAME,'tenant':'synthetic-private'},
        'annotations':{'description':'synthetic-private-body'}}]}


def test_private_auth_content_free_delivery_and_resolved():
    delivered=[]
    def send(notice): delivered.append(notice); return 'provider_accepted'
    with client(send) as api:
        assert api.post('/alerts',json=body()).status_code==403
        for status in ('firing','resolved'):
            response=api.post('/alerts',headers=HEADERS,json=body(status))
            assert response.json()=={'provider_accepted':1,'recipient_read_verified':False}
        assert [notice.status for notice in delivered]==['firing','resolved']
        assert all('synthetic-private' not in notice.text() for notice in delivered)
        metrics=api.get('/metrics',headers=HEADERS).text
        assert 'outcome="provider_accepted"} 2' in metrics
        assert 'synthetic-private' not in metrics and TOKEN not in metrics
        assert api.get('/health',headers=HEADERS).json()['smtp_delivery_verified'] is False


def test_every_item_validated_before_any_delivery_and_duplicates_collapsed():
    delivered=[]
    def send(notice): delivered.append(notice); return 'provider_accepted'
    with client(send) as api:
        payload=body()
        payload['alerts'].append({'status':'firing','labels':{'alertname':'unregistered'}})
        assert api.post('/alerts',headers=HEADERS,json=payload).status_code==400
        assert not delivered
        payload=body(); payload['alerts']*=3
        assert api.post('/alerts',headers=HEADERS,json=payload).json()['provider_accepted']==1
        assert len(delivered)==1


def test_bounded_payload_and_unconfirmed_delivery_hide_raw_errors():
    def fail(notice): raise NotificationError('synthetic-secret-url-password')
    with client(fail) as api:
        response=api.post('/alerts',headers=HEADERS,json=body())
        assert response.status_code==503 and 'synthetic-secret' not in response.text
        assert 'outcome="delivery_unknown"} 1' in api.get('/metrics',headers=HEADERS).text
        response=api.post('/alerts',headers={**HEADERS,'Content-Type':'application/json'},content=b'x'*65537)
        assert response.status_code==413
        assert api.get('/health',headers={**HEADERS,'Host':'evil.invalid'}).status_code==400


def test_busy_receiver_has_no_unbounded_send_queue():
    started=threading.Event(); release=threading.Event()
    def send(notice):
        started.set()
        assert release.wait(5)
        return 'provider_accepted'
    with client(send) as api,ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(api.post,'/alerts',headers=HEADERS,json=body())
        try:
            assert started.wait(3)
            assert api.post('/alerts',headers=HEADERS,json=body()).status_code==429
        finally: release.set()
        assert first.result(timeout=3).status_code==200


def test_nonlocal_client_is_denied_even_with_correct_token():
    api=TestClient(create_alert_receiver(TOKEN,lambda notice:'provider_accepted'),
        base_url='http://127.0.0.1',client=('192.0.2.1',1234))
    assert api.post('/alerts',headers=HEADERS,json=body()).status_code==403


@pytest.mark.asyncio
async def test_cancelled_request_keeps_slot_until_actual_delivery_finishes():
    started=threading.Event(); release=threading.Event()
    def send(notice): started.set(); release.wait(5); return 'provider_accepted'
    app=create_alert_receiver(TOKEN,send)
    transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234))
    async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as api:
        task=asyncio.create_task(api.post('/alerts',headers=HEADERS,json=body()))
        assert await asyncio.to_thread(started.wait,3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        try:
            assert (await api.post('/alerts',headers=HEADERS,json=body())).status_code==429
        finally: release.set()
        for _ in range(50):
            metrics=(await api.get('/metrics',headers=HEADERS)).text
            if 'outcome="provider_accepted"} 1' in metrics: break
            await asyncio.sleep(.02)
        else: pytest.fail('Owned delivery did not finish after cancellation')
