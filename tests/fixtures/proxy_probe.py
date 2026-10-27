"""Synthetic protocol peer for real NGINX template tests; no app data or keys."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import time


class Probe(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(b'id: 1\ndata: first\n\n')
            self.wfile.flush()
            time.sleep(2)
            self.wfile.write(b'id: 2\ndata: done\n\n')
            self.wfile.flush()
            self.close_connection = True
            return
        payload = json.dumps({
            'host': self.headers.get('Host'),
            'proto': self.headers.get('X-Forwarded-Proto'),
            'forwarded': self.headers.get('X-Forwarded-For'),
            'last_event_id': self.headers.get('Last-Event-ID'),
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 1024:
            self.send_error(400)
            return
        self.rfile.read(size)
        payload = b'{"status":"pending","synthetic":true}'
        self.send_response(202)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8000), Probe).serve_forever()
