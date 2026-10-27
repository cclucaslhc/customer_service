"""Real verified HTTPS/cookie/CSRF cross-guest smoke; never prints credentials."""
import http.client
import argparse
from http.cookies import SimpleCookie
import json
from pathlib import Path
import socket
import ssl
import uuid

STATE=Path('/home/labops/cs-lab/state')
NODES=('172.24.168.13','172.24.168.125')


def main(nodes=NODES, port=443):
    if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable guest required')
    context=ssl.create_default_context(cafile=str(STATE/'pg-ca.crt'))
    public=json.loads((STATE/'public.json').read_text())
    cookies={}
    csrf=None
    def request(node,method,path,body=None,extra=None):
        connection=http.client.HTTPSConnection(node,port,context=context,timeout=45)
        headers={'Host':'cs-lab.internal','Origin':public['origin'],'Content-Type':'application/json'}
        if cookies: headers['Cookie']='; '.join(key+'='+value for key,value in cookies.items())
        if csrf: headers['X-CSRF-Token']=csrf
        headers.update(extra or {})
        try:
            connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
            response=connection.getresponse()
            for key,value in response.getheaders():
                if key.lower()=='set-cookie':
                    parsed=SimpleCookie()
                    parsed.load(value)
                    cookies.update({name:item.value for name,item in parsed.items()})
            raw=response.read(1048577)
            assert len(raw)<=1048576,'Probe response limit exceeded'
            return response.status,raw.decode()
        finally:
            connection.close()
    status,body=request(nodes[0],'POST','/api/auth/login',{
        'username':'synthetic.lab.customer','password':'synthetic-password-only','tenant_id':public['tenant_id']})
    if status!=200:
        try:
            detail=json.loads(body).get('detail','unspecified')
        except json.JSONDecodeError:
            detail='non-JSON response'
        raise AssertionError(f'Node1 synthetic login failed, status={status}, detail={detail}')
    csrf=json.loads(body)['csrf_token']
    status,body=request(nodes[0],'POST','/api/chat/sessions',{})
    assert status==200,'Node1 session creation failed'
    sid=json.loads(body)['id']
    # An empty session ID is intentionally lazy: no DB row until first enqueue.
    status,body=request(nodes[1],'GET','/api/auth/me')
    assert status==200 and json.loads(body)['principal']['tenant_id']==public['tenant_id'],'Cross-node identity failed'
    rid=str(uuid.uuid4())
    status,body=request(nodes[1],'POST',f'/api/chat/sessions/{sid}/messages',{
        'content':'Synthetic cross-VM acceptance question.','request_id':rid})
    assert status==202,'Node2 durable enqueue failed'
    events_url=json.loads(body)['events_url']
    status,body=request(nodes[0],'GET','/api/chat/sessions/'+sid)
    assert status==200 and json.loads(body)['session']['id']==sid,'Cross-node durable session failed'
    status,events=request(nodes[0],'GET',events_url)
    assert status==200 and events.count('event: done')==1 and 'Durable completion.' in events,'Node1 SSE completion failed'
    cursors=[int(line[3:].strip()) for line in events.splitlines() if line.startswith('id:')]
    assert cursors and len(cursors)==len(set(cursors))
    status,replay=request(nodes[1],'GET',events_url,extra={'Last-Event-ID':str(cursors[0])})
    assert status==200 and replay.count('event: done')==1,'Cross-node SSE replay failed'
    status,body=request(nodes[1],'GET','/api/chat/sessions/'+sid)
    history=json.loads(body)['messages']
    assert status==200 and [row['role'] for row in history]==['user','assistant'],'Duplicate/lost message'
    print(json.dumps({'https_verified':True,'cookie_csrf_cross_node':len(set(nodes))==2,'enqueue_node':nodes[1],
        'sse_node':nodes[0],'replay_node':nodes[1],'assistant_messages':1,
        'run_id':rid,'port':port,'scope':'synthetic shared-PG two-guest smoke, not production HA'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--survivor-only',action='store_true')
    parser.add_argument('--load-balancer',action='store_true')
    args=parser.parse_args()
    if args.survivor_only and args.load_balancer:
        parser.error('Select one topology')
    main((NODES[0],NODES[0]) if args.load_balancer else ((NODES[1],NODES[1]) if args.survivor_only else NODES),
         8443 if args.load_balancer else 443)
