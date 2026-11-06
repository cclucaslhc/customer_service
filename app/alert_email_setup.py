"""Local operator-only, same-origin Gmail setup; no send/test endpoint."""
import asyncio
import ipaddress
import json
import secrets
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.alert_email_config import gmail_receiver


def create_setup_app(store,port):
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Explicit unprivileged loopback port required')
    origin=f'http://127.0.0.1:{port}'
    token=secrets.token_urlsafe(32)
    app=FastAPI(openapi_url=None,docs_url=None,redoc_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1'])

    @app.middleware('http')
    async def boundary(request,call_next):
        try: local=request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError: local=False
        if not local or (request.headers.get('origin') not in (None,origin)):
            return JSONResponse({'error':'local_same_origin_only'},status_code=403)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','Content-Security-Policy':
            "default-src 'none'; script-src 'nonce-"+token+"'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.get('/',response_class=HTMLResponse)
    def page():
        return HTMLResponse('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Gmail 告警安全配置</title>
<style>body{font:16px system-ui;background:#f4f6fa;color:#172033;margin:0;padding:32px}main{max-width:560px;margin:auto;background:white;padding:28px;border-radius:16px}label{display:block;margin-top:20px}input{box-sizing:border-box;width:100%;padding:12px;margin-top:8px}button{margin-top:24px;padding:12px 20px;background:#245bcc;color:white;border:0;border-radius:6px}p{line-height:1.7}#status{white-space:pre-wrap}</style>
<main><h1>Gmail 告警配置</h1><p>仅在本机操作。请使用 Gmail 的16位应用专用密码，不要使用登录密码。配置将加密保存；保存本身不会发送邮件。</p>
<form id="setup" autocomplete="off"><label>发信 Gmail 账号<input id="account" type="email" required autocomplete="off"></label>
<label>收件邮箱<input id="recipient" type="email" required autocomplete="off"></label>
<label>Gmail 应用专用密码<input id="password" type="password" required autocomplete="new-password" spellcheck="false"></label>
<button id="save">保存加密配置</button></form><p id="status" role="status"></p>
<p>不采集公司文档或聊天内容。不要在聊天里发送密码。配置后请告知助手继续真实告警验收。</p></main>
<script nonce="TOKEN">const form=document.querySelector('#setup');form.addEventListener('submit',async e=>{e.preventDefault();const button=document.querySelector('#save');button.disabled=true;try{const response=await fetch('/configure',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':'TOKEN'},body:JSON.stringify({account:document.querySelector('#account').value,recipient:document.querySelector('#recipient').value,password:document.querySelector('#password').value})});document.querySelector('#status').textContent=response.ok?'已加密保存，尚未发送邮件。':response.status===409?'已有配置，未覆盖。请联系助手进行受控更新。':'配置失败，请核对邮箱与16位应用密码。';}catch{document.querySelector('#status').textContent='本机服务未连接，未确认保存。';}finally{document.querySelector('#password').value='';button.disabled=false;}});</script></html>'''.replace('TOKEN',token))

    @app.post('/configure')
    async def configure(request:Request):
        supplied=request.headers.get('x-setup-token','')
        if (request.headers.get('origin')!=origin or len(supplied)>64 or not supplied.isascii()
                or not secrets.compare_digest(supplied,token)):
            return JSONResponse({'error':'setup_authorization_required'},status_code=403)
        if request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'error':'json_required'},status_code=415)
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>8192: return JSONResponse({'error':'request_too_large'},status_code=413)
        try:
            values=json.loads(raw)
            if not isinstance(values,dict) or set(values)!={'account','recipient','password'}: raise ValueError()
            receiver=gmail_receiver(**values)
            await asyncio.to_thread(store.save_new,receiver)
        except FileExistsError:
            return JSONResponse({'error':'already_configured'},status_code=409)
        except Exception:
            return JSONResponse({'error':'invalid_or_unavailable_configuration'},status_code=400)
        return {'configured':True,'sent':False}
    return app
