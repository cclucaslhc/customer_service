"""Same-host authenticated Alertmanager receiver with fixed, content-free output."""
import asyncio
import ipaddress
import json
import secrets
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.alert_notifications import AlertNotice,NotificationError


def create_alert_receiver(token,sender):
    if not isinstance(token,str) or not 32<=len(token)<=128 or not token.isascii() or not token.isalnum():
        raise ValueError('Explicit private alphanumeric receiver token required')
    app=FastAPI(openapi_url=None,docs_url=None,redoc_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1'])
    lock=asyncio.Lock()
    counts={'provider_accepted':0,'delivery_unknown':0,'provider_rejected':0}

    @app.middleware('http')
    async def boundary(request,call_next):
        try: local=request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError: local=False
        supplied=request.headers.get('authorization','')
        if (not local or len(supplied)>160 or not supplied.isascii()
                or not secrets.compare_digest(supplied,'Bearer '+token)):
            return JSONResponse({'error':'private_receiver_authorization_required'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response

    @app.get('/health')
    def health(): return {'receiver_alive':True,'smtp_delivery_verified':False}

    @app.get('/metrics',response_class=PlainTextResponse)
    def metrics():
        return PlainTextResponse('# TYPE customer_service_notification_delivery_total counter\n'+
            ''.join(f'customer_service_notification_delivery_total{{outcome="{key}"}} {value}\n'
                for key,value in counts.items()),media_type='text/plain; version=0.0.4')

    async def deliver(notices):
        try:
            for notice in notices:
                result=await asyncio.to_thread(sender,notice)
                if result!='provider_accepted': raise NotificationError('notification_delivery_unknown')
                counts['provider_accepted']+=1
        except NotificationError as error:
            counts['provider_rejected' if error.code=='notification_provider_rejected' else 'delivery_unknown']+=1
            return JSONResponse({'error':'notification_not_confirmed'},status_code=503)
        except Exception:
            counts['delivery_unknown']+=1
            return JSONResponse({'error':'notification_not_confirmed'},status_code=503)
        return JSONResponse({'provider_accepted':len(notices),'recipient_read_verified':False})

    @app.post('/alerts')
    async def alerts(request:Request):
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'error':'json_required'},status_code=415)
        raw=bytearray()
        async for block in request.stream():
            raw.extend(block)
            if len(raw)>65536: return JSONResponse({'error':'request_too_large'},status_code=413)
        try:
            value=json.loads(raw)
            batch=value['alerts']
            if not isinstance(batch,list) or not 1<=len(batch)<=32: raise ValueError()
            notices=[]
            for item in batch:
                notice=AlertNotice(item['labels']['alertname'],item['status'])
                notice.text() # Validate every item before any outbound delivery.
                if notice not in notices: notices.append(notice)
            # The shipped Alertmanager policy groups by alertname. At most one
            # firing and one resolved notification per group, never raw labels.
            if len({notice.alert for notice in notices})!=1: raise ValueError()
        except Exception:
            return JSONResponse({'error':'invalid_registered_alert_group'},status_code=400)
        try: await asyncio.wait_for(lock.acquire(),timeout=.05)
        except asyncio.TimeoutError:
            return JSONResponse({'error':'receiver_busy'},status_code=429)
        job=asyncio.create_task(deliver(notices))
        # Disconnect/cancellation cannot free the slot while an SMTP thread is
        # still sending. The owned background task releases it on real completion.
        job.add_done_callback(lambda completed:lock.release())
        return await asyncio.shield(job)
    return app
