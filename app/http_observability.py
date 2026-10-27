"""Bounded, content-free HTTP telemetry; no public exporter or credential discovery."""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import math
from threading import Lock
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from app.metrics_export import MetricsRenderLimit


request_id_context: ContextVar[str | None] = ContextVar('request_id', default=None)
BUCKETS = (.01, .05, .1, .5, 1., 5., 15., 30., 60., 300.)
METHODS = frozenset(('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'))


def json_log_sink(record):
    # Only middleware-generated allowlisted fields reach this logger. No exc_info.
    logging.getLogger('customer_service.http').info(json.dumps(record, separators=(',', ':')))


class HttpTelemetry:
    """Per-process metrics, fixed bucket count and registered route templates only.

    Logging is explicit opt-in. Export snapshots through a private collector, not
    a public API. Sinks must be nonblocking (e.g. a bounded logging QueueHandler).
    """
    def __init__(self, *, sink=None, max_series=4096):
        if type(max_series) is not int or not 1 <= max_series <= 16384:
            raise ValueError('Invalid HTTP metric series limit')
        self.sink = sink
        self.max_series = max_series
        self._lock = Lock()
        self._series = {}
        self._dropped = 0
        self._samples_dropped = 0
        self._exports = {name: 0 for name in ('busy', 'timeout', 'failure', 'oversize', 'cancelled', 'success', 'closed')}
        self.tracing = None
        self.budget_metrics = None
        self.circuit_metrics = None

    def record_export(self, outcome):
        with self._lock:
            self._exports[outcome] += 1

    def record(self, *, route, method, status, outcome, stream, duration, request_id, trace_id=None):
        key = (route, method, f'{status // 100}xx' if 100 <= status < 600 else 'none', outcome, stream)
        with self._lock:
            if key not in self._series and len(self._series) >= self.max_series:
                self._samples_dropped += 1
            else:
                values = self._series.setdefault(key, [0, 0., [0] * (len(BUCKETS) + 1)])
                values[0] += 1
                values[1] += duration
                for i, upper in enumerate((*BUCKETS, math.inf)):
                    values[2][i] += duration <= upper
        if self.sink is not None:
            record = {'timestamp': datetime.now(timezone.utc).isoformat(),
                      'level': 'ERROR' if outcome == 'error' or status >= 500 else 'INFO',
                      'service': 'customer-service', 'event': 'http.completed',
                      'request_id': request_id, 'route': route, 'method': method,
                      'status': status, 'outcome': outcome, 'stream': stream,
                      'duration_ms': round(duration * 1000, 3)}
            if trace_id is not None:
                record['trace_id'] = trace_id
            try:
                self.sink(record)
            except Exception:
                # Never recurse into a broken logger or serialize its exception.
                with self._lock:
                    self._dropped += 1

    def snapshot(self):
        with self._lock:
            return {'dropped_logs': self._dropped, 'dropped_samples': self._samples_dropped,
                    'exports': dict(self._exports), 'buckets': (*BUCKETS, math.inf),
                    'series': [{'route': k[0], 'method': k[1], 'status_class': k[2],
                                'outcome': k[3], 'stream': k[4], 'count': v[0],
                                'duration_sum': v[1], 'bucket_counts': tuple(v[2])}
                               for k, v in self._series.items()]}

    def prometheus(self, *, max_bytes=None, deadline=None, cancel_event=None):
        """LF-terminated text for a private collector; no IDs or content labels."""
        snapshot = self.snapshot()
        counter = 'customer_service_http_requests_total'
        lines = []
        size = 0
        def emit(*items):
            nonlocal size
            for item in items:
                if cancel_event is not None and cancel_event.is_set():
                    raise MetricsRenderLimit('cancelled')
                if deadline is not None and perf_counter() >= deadline:
                    raise MetricsRenderLimit('deadline')
                size += len(item.encode('utf-8')) + 1
                if max_bytes is not None and size > max_bytes:
                    raise MetricsRenderLimit('oversize')
                lines.append(item)
        emit(f'# HELP {counter} HTTP request terminal observations, including failures and disconnects.',
             f'# TYPE {counter} counter')
        def labels(row, *, upper=None):
            pairs = [(name, row[name]) for name in ('route', 'method', 'status_class', 'outcome')]
            if upper is None:
                pairs.append(('stream', 'sse' if row['stream'] else 'http'))
            else:
                pairs.append(('le', '+Inf' if math.isinf(upper) else str(upper)))
            return '{' + ','.join(name + '=' + json.dumps(str(value), ensure_ascii=False) for name, value in pairs) + '}'
        for row in snapshot['series']:
            emit(f'{counter}{labels(row)} {row["count"]}')
        for stream, name in ((False, 'customer_service_http_request_duration_seconds'),
                             (True, 'customer_service_http_sse_connection_duration_seconds')):
            emit(f'# HELP {name} Time through ASGI request termination in seconds.',
                 f'# TYPE {name} histogram')
            for row in snapshot['series']:
                if row['stream'] != stream:
                    continue
                # Remove the stream label from histograms: the name selects it.
                base = labels(row).replace(',stream="sse"', '').replace(',stream="http"', '')
                for upper, count in zip(snapshot['buckets'], row['bucket_counts']):
                    emit(f'{name}_bucket{labels(row, upper=upper)} {count}')
                emit(f'{name}_sum{base} {row["duration_sum"]}', f'{name}_count{base} {row["count"]}')
        name = 'customer_service_http_log_sink_failures_total'
        emit(f'# HELP {name} Exceptions raised by the configured log sink.',
             f'# TYPE {name} counter', f'{name} {snapshot["dropped_logs"]}')
        if self.sink is not None and hasattr(self.sink,'snapshot'):
            log_export = self.sink.snapshot()
            name = 'customer_service_http_log_records_total'
            emit(f'# HELP {name} Bounded allowlisted JSON log export outcomes.',
                 f'# TYPE {name} counter')
            for outcome in ('written','failed','dropped','rejected'):
                emit(f'{name}{{outcome="{outcome}"}} {log_export[outcome]}')
            for name, value in (('customer_service_http_log_queue_depth',log_export['queue_depth']),
                                ('customer_service_http_log_export_worker_alive',int(log_export['worker_alive']))):
                emit(f'# HELP {name} Process-local bounded log exporter state.',
                     f'# TYPE {name} gauge',f'{name} {value}')
        name = 'customer_service_http_metric_samples_dropped_total'
        emit(f'# HELP {name} HTTP samples rejected by the configured series cap.',
             f'# TYPE {name} counter', f'{name} {snapshot["dropped_samples"]}')
        name = 'customer_service_metrics_exports_total'
        emit(f'# HELP {name} Private metric export outcomes; timeouts do not free running worker slots.',
             f'# TYPE {name} counter')
        for outcome, count in snapshot['exports'].items():
            emit(f'{name}{{outcome="{outcome}"}} {count}')
        if self.tracing is not None:
            tracing = self.tracing.snapshot()
            name = 'customer_service_trace_spans_total'
            emit(f'# HELP {name} Bounded trace processing outcomes in this process.',f'# TYPE {name} counter')
            for outcome in ('exported','failed','dropped','rejected','instrumentation_failed'):
                emit(f'{name}{{outcome="{outcome}"}} {tracing[outcome]}')
            for name, value in (('customer_service_trace_queue_depth',tracing['queue_depth']),
                                ('customer_service_trace_export_worker_alive',int(tracing['worker_alive']))):
                emit(f'# HELP {name} Private trace exporter process-local state.',f'# TYPE {name} gauge',f'{name} {value}')
        if self.budget_metrics is not None:
            emit(*self.budget_metrics.prometheus(deadline=deadline,cancel_event=cancel_event))
        if self.circuit_metrics is not None:
            emit(*self.circuit_metrics.prometheus(deadline=deadline,cancel_event=cancel_event))
        return '\n'.join(lines) + '\n'


class HttpObservationMiddleware:
    def __init__(self, app, *, telemetry, routes, tracing=None):
        self.app, self.telemetry = app, telemetry
        self.tracing = tracing
        self.routes = frozenset(routes)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        # Caller IDs are never authoritative (even syntactically valid UUIDs).
        request_id = str(uuid4())
        scope.setdefault('state', {})['request_id'] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status, stream, finished, disconnected = 0, False, False, False
        outcome = 'incomplete'
        trace_scope = trace_span = trace_id = None
        if self.tracing is not None:
            from app.tracing import capture_carrier
            method = scope.get('method','')
            trace_scope = self.tracing.scope('http.request',root=True,request_id=request_id,method=method)
            trace_span = trace_scope.__enter__()
            carrier = capture_carrier()
            trace_id = carrier['trace_id'] if carrier else None

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message['type'] == 'http.disconnect':
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal status, stream, finished
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'x-request-id']
                stream = any(k.lower() == b'content-type' and v.lower().split(b';')[0].strip() == b'text/event-stream'
                             for k, v in headers)
                message = {**message, 'headers': [*headers, (b'x-request-id', request_id.encode('ascii'))]}
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                finished = True

        try:
            await self.app(scope, observed_receive, observed_send)
            outcome = 'completed' if finished else ('disconnected' if disconnected else 'incomplete')
        except asyncio.CancelledError:
            outcome = 'disconnected' if disconnected else 'cancelled'
            raise
        except Exception:
            outcome = 'error'
            raise
        finally:
            route = getattr(scope.get('route'), 'path', None)
            route = route if route in self.routes else '__unmatched__'
            method = scope.get('method', '')
            method = method if method in METHODS else 'OTHER'
            try:
                self.telemetry.record(route=route, method=method, status=status,
                    outcome=outcome, stream=stream, duration=max(0., perf_counter()-started), request_id=request_id,
                    trace_id=trace_id)
            finally:
                if trace_scope is not None:
                    self.tracing.finish_http(trace_span,route=route,status=status,outcome=outcome,stream=stream)
                    trace_scope.__exit__(None,None,None)
                request_id_context.reset(token)


class ObservedFastAPI(FastAPI):
    """Wrap outside ServerErrorMiddleware to observe generated 500s as well."""
    def __init__(self, *, telemetry=None, tracing=None, **kwargs):
        super().__init__(**kwargs)
        self.state.http_telemetry = telemetry if telemetry is not None else HttpTelemetry()
        self.state.tracing = tracing
        self.state.http_telemetry.tracing = tracing

    def build_middleware_stack(self):
        return HttpObservationMiddleware(super().build_middleware_stack(),
            telemetry=self.state.http_telemetry,
            tracing=self.state.tracing,
            routes=[route.path for route in self.routes if hasattr(route, 'path')])
