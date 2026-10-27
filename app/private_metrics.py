"""Opt-in authenticated loopback exporter. No credential or server autodiscovery."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import logging
import math
import re
import socket
from time import monotonic

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol
from app.metrics_export import BoundedMetricsExporter


@dataclass(frozen=True)
class MetricsPolicy:
    token: bytes = field(repr=False)
    port: int = 0
    export_timeout: float = 1.
    max_export_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.token, bytes) or not re.fullmatch(rb'[A-Za-z0-9_-]{43,256}', self.token):
            raise ValueError('Explicit dedicated metrics credential required')
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError('Invalid private metrics port')
        if type(self.export_timeout) not in (int, float) or not math.isfinite(self.export_timeout) or not .05 <= self.export_timeout <= 5:
            raise ValueError('Invalid metrics export timeout')
        if type(self.max_export_bytes) is not int or not 1024 <= self.max_export_bytes <= 16 * 1024 * 1024:
            raise ValueError('Invalid metrics export size limit')


class MetricsApplication:
    """Separate application, never mounted on the public business router."""
    def __init__(self, telemetry, policy, *, port):
        self.telemetry, self.policy, self.port = telemetry, policy, port
        self.exporter = BoundedMetricsExporter(telemetry, timeout=policy.export_timeout, max_bytes=policy.max_export_bytes)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] != 'http':
            return
        headers = scope.get('headers', [])
        def values(name):
            return [v for k, v in headers if k.lower() == name]
        try:
            local = ipaddress.ip_address(scope.get('client', ('', 0))[0]).is_loopback
        except (ValueError, TypeError):
            local = False
        hosts = values(b'host')
        origins = values(b'origin') + values(b'referer') + values(b'sec-fetch-site')
        auth = values(b'authorization')
        expected = b'Bearer ' + self.policy.token
        authorized = (len(auth) == 1 and len(auth[0]) == len(expected)
                      and hmac.compare_digest(auth[0], expected))
        if (not local or origins or len(hosts) != 1 or
                hosts[0] not in (f'127.0.0.1:{self.port}'.encode(), f'localhost:{self.port}'.encode())):
            status, content = 403, b'Private metrics access denied\n'
        elif not authorized:
            status, content = 401, b'Private metrics authentication required\n'
        elif scope.get('path') != '/metrics' or scope.get('query_string', b''):
            status, content = 404, b'Not found\n'
        elif scope.get('method') != 'GET':
            status, content = 405, b'Method not allowed\n'
        else:
            try:
                content = await self.exporter.render()
                status = 200
            except Exception:
                # No raw exporter exception or partial historical success.
                status, content = 503, b'Private metrics unavailable\n'
        headers = [(b'content-type', b'text/plain; version=0.0.4; charset=utf-8' if status == 200 else b'text/plain; charset=utf-8'),
                   (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                   (b'content-length', str(len(content)).encode())]
        if status == 401:
            headers.append((b'www-authenticate', b'Bearer'))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': content})


class _PrivateServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Parent API owns process signals; this secondary listener must not steal them.
        yield


class _PrivateConfig(uvicorn.Config):
    def configure_logging(self):
        # Uvicorn's normal access_log=False clears the GLOBAL access logger's
        # handlers. The secondary listener must not mute the parent API.
        pass


class _PrivateProtocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.access_log = False
        # Protocol exceptions may contain raw headers/paths. Do not emit them.
        private_logger = logging.Logger('customer_service.metrics.protocol')
        private_logger.disabled = True
        self.logger = self.access_logger = private_logger


@asynccontextmanager
async def private_metrics_listener(telemetry, policy):
    """Owned listener lifecycle; bind failure prevents claiming it is available.

    IPv4 loopback only, no proxy trust, no access log, no external publishing.
    Port zero is useful for isolated tests; production must assign per-instance
    stable ports and a dedicated token supplied explicitly by its operator.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    task = server = application = None
    try:
        # Never SO_REUSEADDR on Windows: do not share another process's binding.
        listener.bind(('127.0.0.1', policy.port))
        listener.listen(32)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        application = MetricsApplication(telemetry, policy, port=port)
        server = _PrivateServer(_PrivateConfig(application, host='127.0.0.1', port=port,
            http=_PrivateProtocol, ws='none', lifespan='off', proxy_headers=False,
            access_log=False, log_config=None, log_level=None,
            timeout_keep_alive=2, timeout_graceful_shutdown=2,
            limit_concurrency=16, h11_max_incomplete_event_size=8192))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        deadline = monotonic() + 5
        while not server.started:
            if task.done() or monotonic() >= deadline:
                raise RuntimeError('Private metrics listener unavailable')
            await asyncio.sleep(.01)
        yield port
    finally:
        if server is not None:
            server.should_exit = True
        try:
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            listener.close()
            if application is not None:
                await application.exporter.aclose()
