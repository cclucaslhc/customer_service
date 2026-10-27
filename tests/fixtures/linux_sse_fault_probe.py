"""Verified HTTPS cross-guest cancellation and a real subscriber disconnect."""
import argparse
import http.client
from http.cookies import SimpleCookie
import json
from pathlib import Path
import socket
import ssl
import time
import uuid

STATE=Path('/home/labops/cs-lab/state')
NODES=('172.24.168.13','172.24.168.125')


def frames(text):
    result=[]
    for block in text.replace('\r\n','\n').split('\n\n'):
        item={}
        for line in block.splitlines():
            if line.startswith('id:'):
                value=line[3:].strip()
                if not value.isascii() or not value.isdecimal() or len(value)>7:
                    raise ValueError('Invalid SSE cursor')
                item['id']=int(value)
            elif line.startswith('event:'):
                item['event']=line[6:].strip()
        if item: result.append(item)
    return result


class Client:
    def __init__(self):
        if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
            raise RuntimeError('Owned disposable guest required')
        self.context=ssl.create_default_context(cafile=str(STATE/'pg-ca.crt'))
        self.public=json.loads((STATE/'public.json').read_text())
        self.cookies={}
        self.csrf=None

    def open(self,node,method,path,body=None,extra=None):
        if node not in NODES: raise ValueError('Explicit guest address required')
        connection=http.client.HTTPSConnection(node,context=self.context,timeout=35)
        headers={'Host':'cs-lab.internal','Origin':self.public['origin'],'Content-Type':'application/json'}
        if self.cookies: headers['Cookie']='; '.join(key+'='+value for key,value in self.cookies.items())
        if self.csrf: headers['X-CSRF-Token']=self.csrf
        headers.update(extra or {})
        connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
        response=connection.getresponse()
        for key,value in response.getheaders():
            if key.lower()=='set-cookie':
                parsed=SimpleCookie()
                parsed.load(value)
                self.cookies.update({name:item.value for name,item in parsed.items()})
        return connection,response

    def request(self,*args,**kwargs):
        connection,response=self.open(*args,**kwargs)
        try:
            raw=response.read(1048577)
            if len(raw)>1048576: raise ValueError('Probe response limit exceeded')
            return response.status,raw.decode()
        finally:
            response.close()
            connection.close()

    def login(self):
        status,text=self.request(NODES[1],'POST','/api/auth/login',{
            'username':'synthetic.lab.admin','password':'synthetic-password-only','tenant_id':self.public['tenant_id']})
        assert status==200,f'Synthetic HTTP login failed, status={status}'
        self.csrf=json.loads(text)['csrf_token']

    def enqueue(self):
        status,text=self.request(NODES[1],'POST','/api/chat/sessions',{})
        assert status==200
        sid=json.loads(text)['id']
        rid=str(uuid.uuid4())
        status,text=self.request(NODES[1],'POST',f'/api/chat/sessions/{sid}/messages',{
            'content':'Synthetic cancel/disconnect question.','request_id':rid})
        assert status==202
        return sid,rid,json.loads(text)['events_url']


def provider_frame(response):
    block=''
    total=0
    while total<262144:
        raw=response.readline(16385)
        if not raw or len(raw)>16384: raise RuntimeError('No bounded running-provider frame')
        total+=len(raw)
        block+=raw.decode()
        if raw in (b'\n',b'\r\n'):
            items=frames(block)
            if any(item.get('event')=='provider' for item in items):
                return next(item['id'] for item in items if item.get('event')=='provider')
            block=''
    raise RuntimeError('Provider frame budget exceeded')


def scenario(client,case):
    sid,rid,url=client.enqueue()
    connection,response=client.open(NODES[0],'GET',url)
    assert response.status==200
    try:
        cursor=provider_frame(response)
        status,text=client.request(NODES[1],'GET',f'/api/chat/sessions/{sid}/runs/{rid}')
        assert status==200 and json.loads(text)['run']['status']=='running','Must test a running Worker, not queued/complete'
        if case=='cancel':
            started=time.monotonic()
            status,text=client.request(NODES[1],'POST',f'/api/chat/sessions/{sid}/runs/{rid}/cancel')
            assert status==200 and json.loads(text)['cancelled'] is True
            remaining=response.read(1048577)
            assert len(remaining)<=1048576
            terminal=frames(remaining.decode())
            assert sum(item.get('event')=='interrupted' for item in terminal)==1
            assert not any(item.get('event')=='done' for item in terminal)
            status,text=client.request(NODES[1],'GET',f'/api/chat/sessions/{sid}/runs/{rid}')
            snapshot=json.loads(text)
            assert status==200 and snapshot['run']['status']=='interrupted'
            assert snapshot['run']['draft']=='' and [m['role'] for m in snapshot['messages']]==['user']
            return {'case':case,'run_id':rid,'running_before_cancel':True,'assistant_messages':0,
                'cancel_to_terminal_seconds':round(time.monotonic()-started,3)}
        # Actively close the live verified TLS subscriber; do not finish reading
        # and pretend a completed stream was a network fault.
        if connection.sock is None: raise RuntimeError('No live subscriber socket')
        connection.sock.shutdown(socket.SHUT_RDWR)
        response.close()
        connection.close()
        status,text=client.request(NODES[1],'GET',f'/api/chat/sessions/{sid}/runs/{rid}')
        current=json.loads(text)['run']
        assert status==200 and current['status']=='running' and current['cancel_requested'] is False
        status,text=client.request(NODES[1],'GET',url,extra={'Last-Event-ID':str(cursor)})
        items=frames(text)
        ids=[item['id'] for item in items if 'id' in item]
        assert status==200 and ids and ids==sorted(set(ids)) and min(ids)>cursor
        assert sum(item.get('event')=='done' for item in items)==1
        status,text=client.request(NODES[0],'GET',f'/api/chat/sessions/{sid}/runs/{rid}')
        snapshot=json.loads(text)
        assert status==200 and snapshot['run']['status']=='complete'
        assert [m['role'] for m in snapshot['messages']]==['user','assistant']
        return {'case':case,'run_id':rid,'disconnected_while_running':True,
            'continued_without_cancel':True,'reconnected_to_other_api':True,'assistant_messages':1,'done_events':1}
    finally:
        response.close()
        connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=('all','cancel','disconnect'),default='all')
    args=parser.parse_args()
    client=Client()
    client.login()
    try:
        for case in (('cancel','disconnect') if args.case=='all' else (args.case,)):
            print(json.dumps(scenario(client,case),sort_keys=True),flush=True)
    finally:
        client.request(NODES[1],'POST','/api/auth/logout')


if __name__=='__main__':
    main()
