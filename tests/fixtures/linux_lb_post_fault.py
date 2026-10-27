"""Real NGINX ambiguous-POST no-replay check on disposable guest loopback."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
STATE=Path('/home/labops/cs-lab/state')


def main():
    if socket.gethostname() != 'cs-s2s6-node1' or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable node1 required')
    counts={'primary':0,'backup':0}
    servers=[]
    process=None
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(STATE/'https.crt',STATE/'https.key')
    client_tls=ssl.create_default_context(cafile=str(STATE/'pg-ca.crt'))
    def handler(name):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(503 if name=='primary' else 200)
                self.send_header('Content-Length','0')
                self.end_headers()
            def do_POST(self):
                length=int(self.headers.get('Content-Length','0'))
                if not 0 <= length <= 1024:
                    raise RuntimeError('Synthetic body limit exceeded')
                self.rfile.read(length)
                counts[name]+=1  # Synthetic committed effect before response loss.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
        return Handler
    with tempfile.TemporaryDirectory(prefix='lb-post-',dir=STATE) as folder:
        work=Path(folder)
        try:
            for name in ('primary','backup'):
                server=ThreadingHTTPServer(('127.0.0.1',0),handler(name))
                server.daemon_threads=True
                server.socket=tls.wrap_socket(server.socket,server_side=True)
                servers.append(server)
                threading.Thread(target=server.serve_forever,daemon=True).start()
            # Reserve a private loopback port; no guest/public listener needed.
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1',0))
                port=reserve.getsockname()[1]
            template=(ROOT/'ops/nginx/customer-service-lb.conf.template').read_text()
            values={'PRIMARY_EDGE':f'127.0.0.1:{servers[0].server_port}',
                    'BACKUP_EDGE':f'127.0.0.1:{servers[1].server_port}',
                    'PRIVATE_LISTEN':f'127.0.0.1:{port}','CANONICAL_HOST':'cs-lab.internal',
                    'TLS_CERTIFICATE_PATH':str(STATE/'https.crt'),
                    'TLS_PRIVATE_KEY_PATH':str(STATE/'https.key'),
                    'UPSTREAM_CA_PATH':str(STATE/'pg-ca.crt'),'APPROVED_UPLOAD_LIMIT':'1m'}
            for name,value in values.items(): template=template.replace('@'+name+'@',value)
            config=work/'nginx.conf'
            config.write_text(f'pid {work}/nginx.pid;\nerror_log {work}/error.log warn;\n'
                'events { worker_connections 32; }\nhttp { access_log off;\n'+
                f'client_body_temp_path {work}/body; proxy_temp_path {work}/proxy;\n'+template+'\n}\n')
            process=subprocess.Popen(['/usr/sbin/nginx','-p',str(work)+'/',
                '-c',str(config),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            # Verify the IP SAN from the actual TLS certificate while the socket
            # connects only to loopback; never disable hostname verification.
            def request(method):
                connection=http.client.HTTPSConnection('172.24.168.13',port,context=client_tls,timeout=5)
                connection._create_connection=lambda *a,**kw:socket.create_connection(('127.0.0.1',port),timeout=5)
                try:
                    connection.request(method,'/synthetic-effect',body='synthetic' if method=='POST' else None,
                        headers={'Host':'cs-lab.internal'})
                    response=connection.getresponse()
                    response.read(1024)
                    return response.status
                finally: connection.close()
            for _ in range(50):
                if process.poll() is not None: raise RuntimeError('Synthetic NGINX startup failed')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError: time.sleep(.1)
            status=request('POST')
            assert status==502, 'Ambiguous POST must surface failure, not synthetic success'
            assert counts=={'primary':1,'backup':0}, 'Ambiguous POST was replayed'
            assert request('GET')==200, 'Healthy backup must still serve safe reads'
            assert counts=={'primary':1,'backup':0}
            print(json.dumps({'post_status':status,'effects':counts,'backup_get_status':200,
                'tls_verified':True,'scope':'synthetic response-loss, not a real business write'}))
        finally:
            if process is not None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__=='__main__': main()
