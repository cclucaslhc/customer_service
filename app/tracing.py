"""Explicit OTel provider, bounded off-thread export, content-free spans/carriers."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from queue import Queue, Empty, Full
import re
from threading import Event, Lock, Thread
from time import monotonic
from uuid import UUID, uuid4
from weakref import WeakSet

from opentelemetry import context, trace
from sqlalchemy import event
from sqlalchemy.engine import Engine
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanLimits, SpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, ALWAYS_ON
from opentelemetry.sdk.trace.export import SpanExportResult

_runtime = ContextVar('safe_trace_runtime', default=None)
_request = ContextVar('safe_trace_request', default=None)
_run = ContextVar('safe_trace_run', default=None)
_job = ContextVar('safe_trace_job', default=None)
_attempt = ContextVar('safe_trace_attempt', default=None)
_TOOLS = frozenset(('search_knowledge', 'get_business_time', 'lookup_order',
                    'lookup_shipping', 'check_refund_eligibility', 'show_contact_form'))
_OBJECT_OPERATIONS = frozenset(('check_ready', 'put', 'get', 'list_versions'))
_NAMES = frozenset(('http.request', 'worker.chat', 'worker.knowledge', 'agent.run',
                    'tool.execute', 'rag.search', 'rag.embed_query', 'rag.vector',
                    'rag.fuse', 'rag.rerank', 'model.request', 'db.query', 'object.request'))
_RAG_STAGES = {'rag.search': 'retrieve', 'rag.embed_query': 'embed_query',
               'rag.vector': 'vector', 'rag.fuse': 'fuse', 'rag.rerank': 'rerank'}
_PROVIDERS = frozenset(('deepseek', 'openai', 'local', 'synthetic', 'unknown'))
_ATTRS = frozenset(('request_id', 'run_id', 'job_id', 'http.request.method', 'http.route',
                    'http.response.status_code', 'outcome', 'stream', 'attempt_id',
                    'tool.name', 'rag.stage', 'model.provider', 'model.stream', 'object.operation'))
_DATABASE_ENGINES = WeakSet()
_DATABASE_ENGINES_LOCK = Lock()


def install_database_tracing(engine):
    """Trace SQLAlchemy cursor execution without exporting SQL or bind values.

    The listeners consult the active private tracing context at execution time,
    so one engine can be shared by API and Worker runtimes without duplicate
    listeners or a global OTel provider.
    """
    if not isinstance(engine, Engine):
        raise ValueError('SQLAlchemy Engine required for database tracing')
    with _DATABASE_ENGINES_LOCK:
        if engine in _DATABASE_ENGINES:
            return
        event.listen(engine, 'before_cursor_execute', _database_before)
        event.listen(engine, 'after_cursor_execute', _database_after)
        event.listen(engine, 'handle_error', _database_error)
        _DATABASE_ENGINES.add(engine)


def _database_before(connection, cursor, statement, parameters, execution_context, executemany):
    runtime = _runtime.get()
    if runtime is None:
        return
    try:
        scope = runtime.scope('db.query')
        span = scope.__enter__()
        execution_context._cs_database_trace = (scope, span, runtime)
    except Exception:
        runtime.processor.count('instrumentation_failed')


def _database_finish(execution_context, error=None):
    active = getattr(execution_context, '_cs_database_trace', None)
    if active is None:
        return
    del execution_context._cs_database_trace
    scope, span, runtime = active
    try:
        if error is None and span is not None:
            span.set_attribute('outcome', 'completed')
        scope.__exit__(type(error) if error is not None else None,
                       error, error.__traceback__ if error is not None else None)
    except Exception:
        runtime.processor.count('instrumentation_failed')


def _database_after(connection, cursor, statement, parameters, execution_context, executemany):
    _database_finish(execution_context)


def _database_error(exception_context):
    if exception_context.execution_context is not None:
        _database_finish(exception_context.execution_context, exception_context.original_exception)


def _safe_attributes(values):
    if set(values) - _ATTRS:
        return False
    try:
        for key in ('request_id','run_id','job_id','attempt_id'):
            if key in values and str(UUID(values[key])) != values[key]:
                return False
        if 'tool.name' in values and values['tool.name'] not in _TOOLS:
            return False
        if 'rag.stage' in values and values['rag.stage'] not in {'retrieve','embed_query','vector','fuse','rerank'}:
            return False
        if 'model.provider' in values and values['model.provider'] not in _PROVIDERS:
            return False
        if 'model.stream' in values and type(values['model.stream']) is not bool:
            return False
        if 'object.operation' in values and values['object.operation'] not in _OBJECT_OPERATIONS:
            return False
        if 'http.request.method' in values and values['http.request.method'] not in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','OTHER'}:
            return False
        if 'outcome' in values and values['outcome'] not in {'completed','error','cancelled','disconnected','incomplete','identity_unavailable','coordination_unavailable'}:
            return False
        if 'http.response.status_code' in values and (type(values['http.response.status_code']) is not int or not 0 <= values['http.response.status_code'] < 600):
            return False
        if 'stream' in values and type(values['stream']) is not bool:
            return False
        if 'http.route' in values and not re.fullmatch(r'[A-Za-z0-9_/{\}.:-]{1,256}',values['http.route']):
            return False
    except (TypeError,ValueError,AttributeError):
        return False
    return True


def validate_carrier(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {'request_id','trace_id','span_id','sampled'}
            or type(value['sampled']) is not bool):
        raise ValueError('Invalid internal trace carrier')
    for name, size in (('trace_id',32),('span_id',16)):
        if not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value[name]) or int(value[name],16)==0:
            raise ValueError('Invalid internal trace carrier')
    try:
        if str(UUID(value['request_id'])) != value['request_id']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid internal trace carrier') from None
    return dict(value)


def capture_carrier():
    if _runtime.get() is None or _request.get() is None:
        return None
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return None
    return validate_carrier({'request_id':_request.get(),'trace_id':f'{span.trace_id:032x}',
                             'span_id':f'{span.span_id:016x}','sampled':bool(span.trace_flags.sampled)})


class _SafeProcessor(SpanProcessor):
    def __init__(self, exporter, capacity):
        self.exporter, self.queue = exporter, Queue(maxsize=capacity)
        self.stop, self.lock = Event(), Lock()
        self.counts = {key:0 for key in ('exported','failed','dropped','rejected','instrumentation_failed')}
        self.thread = Thread(target=self._consume, name='cs-trace-export', daemon=True)
        self.thread.start()

    def count(self, name, count=1):
        with self.lock:
            self.counts[name] += count

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        # Future automatic SDK instrumentation cannot sneak content/events in.
        if (span.name not in _NAMES or span.events or span.links or
                not _safe_attributes(span.attributes or {}) or span.status.description):
            self.count('rejected')
            return
        if self.stop.is_set():
            self.count('dropped')
            return
        try:
            self.queue.put_nowait(span)
        except Full:
            self.count('dropped')

    def _consume(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                span = self.queue.get(timeout=.05)
            except Empty:
                continue
            try:
                result = self.exporter.export((span,))
                self.count('exported' if result == SpanExportResult.SUCCESS else 'failed')
            except Exception:
                self.count('failed')  # Never log exporter payload, headers or exception.
            finally:
                self.queue.task_done()

    def force_flush(self, timeout_millis=1000):
        deadline = monotonic() + timeout_millis / 1000
        while self.queue.unfinished_tasks:
            if monotonic() >= deadline:
                return False
            self.stop.wait(.005) if not self.stop.is_set() else Event().wait(.005)
        return True

    def shutdown(self):
        self.stop.set()
        self.thread.join(timeout=1)
        # Do not call arbitrary exporter.shutdown on the API event loop or
        # concurrently with a blocked export. The explicit exporter owner closes it.


class SafeTracing:
    def __init__(self, exporter, *, capacity=256):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError('Invalid trace queue capacity')
        self.provider = TracerProvider(resource=Resource({'service.name':'customer-service'}),
            sampler=ParentBased(ALWAYS_ON),shutdown_on_exit=False,
            span_limits=SpanLimits(max_attributes=16,max_events=0,max_links=0,
                max_span_attributes=16,max_event_attributes=0,max_link_attributes=0,
                max_attribute_length=256,max_span_attribute_length=256))
        self.processor = _SafeProcessor(exporter, capacity)
        self.provider.add_span_processor(self.processor)
        self.tracer = self.provider.get_tracer('customer_service.safe', '1')

    @contextmanager
    def scope(self, name, *, root=False, carrier=None, request_id=None, run_id=None, job_id=None,
              method=None, attempt_id=None, tool_name=None, rag_stage=None,
              model_provider=None, model_stream=None, object_operation=None):
        if name not in _NAMES:
            raise ValueError('Unregistered trace operation')
        if name in _RAG_STAGES and rag_stage != _RAG_STAGES[name]:
            raise ValueError('Invalid RAG trace stage')
        carrier = validate_carrier(carrier)
        parent = context.Context() if root else context.get_current()
        if carrier is not None:
            parent = trace.set_span_in_context(trace.NonRecordingSpan(trace.SpanContext(
                int(carrier['trace_id'],16),int(carrier['span_id'],16),True,
                trace.TraceFlags(1 if carrier['sampled'] else 0))), context.Context())
            request_id = carrier['request_id']
        request_id = request_id or (None if root else _request.get())
        run_id = run_id or (None if root else _run.get())
        job_id = job_id or (None if root else _job.get())
        attempt_id = attempt_id or (None if root else _attempt.get())
        attributes = {}
        if request_id:
            attributes['request_id'] = str(UUID(request_id))
        if run_id:
            attributes['run_id'] = str(UUID(run_id))
        if job_id:
            attributes['job_id'] = str(UUID(job_id))
        if attempt_id:
            attributes['attempt_id'] = str(UUID(attempt_id))
        if tool_name is not None:
            attributes['tool.name'] = tool_name
        if rag_stage is not None:
            attributes['rag.stage'] = rag_stage
        if model_provider is not None:
            attributes['model.provider'] = model_provider
        if model_stream is not None:
            attributes['model.stream'] = model_stream
        if object_operation is not None:
            attributes['object.operation'] = object_operation
        if not _safe_attributes(attributes):
            raise ValueError('Invalid trace operation attributes')
        if method:
            attributes['http.request.method'] = method if method in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} else 'OTHER'
        span = None
        attached = None
        runtime_token = request_token = run_token = job_token = attempt_token = None
        try:
            span = self.tracer.start_span(name, context=parent,
                kind=trace.SpanKind.SERVER if name=='http.request' else trace.SpanKind.CONSUMER if name in {'worker.chat','worker.knowledge'} else trace.SpanKind.INTERNAL,
                attributes=attributes)
            attached = context.attach(trace.set_span_in_context(span,parent))
            runtime_token = _runtime.set(self)
            request_token = _request.set(request_id)
            run_token = _run.set(run_id)
            job_token = _job.set(job_id)
            attempt_token = _attempt.set(attempt_id)
        except Exception:
            self.processor.count('instrumentation_failed')
        try:
            yield span
        except BaseException as exc:
            if span:
                try:
                    span.set_attribute('outcome','cancelled' if isinstance(exc,asyncio.CancelledError) else 'error')
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                except Exception:
                    self.processor.count('instrumentation_failed')
            raise
        finally:
            if attempt_token is not None:
                _attempt.reset(attempt_token)
            if run_token is not None:
                _run.reset(run_token)
            if job_token is not None:
                _job.reset(job_token)
            if request_token is not None:
                _request.reset(request_token)
            if runtime_token is not None:
                _runtime.reset(runtime_token)
            if attached is not None:
                context.detach(attached)
            if span is not None:
                try:
                    span.end()
                except Exception:
                    self.processor.count('instrumentation_failed')

    def snapshot(self):
        with self.processor.lock:
            return {**self.processor.counts,'queue_depth':self.processor.queue.qsize(),
                    'worker_alive':self.processor.thread.is_alive()}

    def flush(self, timeout=1):
        return self.processor.force_flush(int(timeout*1000))

    def close(self):
        self.provider.shutdown()

    def finish_http(self, span, *, route, status, outcome, stream):
        if span is None:
            return
        try:
            span.set_attributes({'http.route':route if len(route)<=256 else '__long_route__',
                'http.response.status_code':status,'outcome':outcome,'stream':stream})
            if outcome != 'completed' or status >= 500:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
        except Exception:
            self.processor.count('instrumentation_failed')


@asynccontextmanager
async def agent_scope(run_id):
    runtime = _runtime.get()
    if runtime is None:
        yield
    else:
        with runtime.scope('agent.run',run_id=run_id):
            yield


@contextmanager
def operation_scope(name, *, tool_name=None, rag_stage=None, object_operation=None):
    """Content-free child operation; never inspect arguments or returned values."""
    runtime = _runtime.get()
    if runtime is None:
        yield
        return
    attempt_id = str(uuid4()) if name == 'tool.execute' else None
    with runtime.scope(name, attempt_id=attempt_id, tool_name=tool_name,
                       rag_stage=rag_stage, object_operation=object_operation) as span:
        yield
        if span is not None:
            try:
                span.set_attribute('outcome', 'completed')
            except Exception:
                runtime.processor.count('instrumentation_failed')


def traced_tool(name):
    """Preserve PydanticAI's inspected signature and sync/async execution mode."""
    if name not in _TOOLS:
        raise ValueError('Unregistered trace tool')
    def decorate(function):
        if iscoroutinefunction(function):
            @wraps(function)
            async def async_call(*args, **kwargs):
                with operation_scope('tool.execute', tool_name=name):
                    return await function(*args, **kwargs)
            return async_call
        @wraps(function)
        def sync_call(*args, **kwargs):
            with operation_scope('tool.execute', tool_name=name):
                return function(*args, **kwargs)
        return sync_call
    return decorate


def traced_object_operation(name):
    """Trace only a fixed operation label, never bucket/key/content or SDK errors."""
    if name not in _OBJECT_OPERATIONS:
        raise ValueError('Unregistered object trace operation')
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with operation_scope('object.request', object_operation=name):
                return function(*args, **kwargs)
        return call
    return decorate


@contextmanager
def model_request_scope(provider, *, stream, attempt_id=None):
    """One SDK request, not one Run or guaranteed one low-level HTTP attempt."""
    if provider not in _PROVIDERS or type(stream) is not bool:
        raise ValueError('Invalid model trace labels')
    runtime = _runtime.get()
    if runtime is None:
        yield None
        return
    with runtime.scope('model.request', attempt_id=attempt_id or str(uuid4()),
                       model_provider=provider, model_stream=stream) as span:
        yield span


def finish_model_request(span, outcome):
    if span is None:
        return
    if outcome not in {'completed', 'cancelled', 'incomplete'}:
        raise ValueError('Invalid model trace outcome')
    try:
        span.set_attribute('outcome', outcome)
        if outcome != 'completed':
            span.set_status(trace.Status(trace.StatusCode.ERROR))
    except Exception:
        runtime = _runtime.get()
        if runtime is not None:
            runtime.processor.count('instrumentation_failed')


def mark_failure(outcome):
    runtime = _runtime.get()
    if runtime is None:
        return
    if outcome not in {'error','cancelled','identity_unavailable','coordination_unavailable'}:
        raise ValueError('Invalid trace outcome')
    try:
        span = trace.get_current_span()
        span.set_attribute('outcome',outcome)
        span.set_status(trace.Status(trace.StatusCode.ERROR))
    except Exception:
        runtime.processor.count('instrumentation_failed')
