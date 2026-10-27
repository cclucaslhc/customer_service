"""Opt-in bounded JSON log sink; never serialize request content or exceptions."""
from datetime import datetime, timezone
import json
import math
from queue import Empty, Full, Queue
import re
from threading import Event, Lock, Thread
from time import monotonic, sleep
from uuid import UUID


_REQUIRED = frozenset(('timestamp','level','service','event','request_id','route',
                       'method','status','outcome','stream','duration_ms'))
_OPTIONAL = frozenset(('trace_id',))
_OUTCOMES = frozenset(('completed','error','disconnected','cancelled','incomplete'))
_METHODS = frozenset(('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','OTHER'))


def _safe_record(value):
    if type(value) is not dict or not _REQUIRED <= value.keys() or set(value) - (_REQUIRED | _OPTIONAL):
        return False
    try:
        timestamp = datetime.fromisoformat(value['timestamp'])
        if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            return False
        if value['level'] not in ('INFO','ERROR') or value['service'] != 'customer-service' or value['event'] != 'http.completed':
            return False
        if str(UUID(value['request_id'])) != value['request_id']:
            return False
        if not isinstance(value['route'],str) or not re.fullmatch(r'[A-Za-z0-9_/{\}.:-]{1,256}',value['route']):
            return False
        if value['method'] not in _METHODS or value['outcome'] not in _OUTCOMES:
            return False
        if type(value['status']) is not int or not 0 <= value['status'] < 600 or type(value['stream']) is not bool:
            return False
        if type(value['duration_ms']) not in (float,int) or not math.isfinite(value['duration_ms']) or value['duration_ms'] < 0:
            return False
        if 'trace_id' in value and (type(value['trace_id']) is not str or
                not re.fullmatch('[0-9a-f]{32}',value['trace_id']) or int(value['trace_id'],16) == 0):
            return False
    except (ValueError,TypeError,AttributeError):
        return False
    return True


class BoundedJsonLogSink:
    """One writer thread, bounded queue and explicit owner lifecycle.

    The writer receives one complete JSON line; it must not enrich the line with
    raw request data. A stuck writer cannot be killed, so close reports failure.
    """
    def __init__(self, writer, *, capacity=1024, max_bytes=2048):
        if not callable(writer) or type(capacity) is not int or not 1 <= capacity <= 8192:
            raise ValueError('Explicit bounded log writer required')
        if type(max_bytes) is not int or not 512 <= max_bytes <= 16384:
            raise ValueError('Invalid log line byte limit')
        self.writer = writer
        self.max_bytes = max_bytes
        self.queue = Queue(maxsize=capacity)
        self.stop = Event()
        self.lock = Lock()
        self.counts = {'written':0,'failed':0,'dropped':0,'rejected':0}
        self.thread = Thread(target=self._consume,name='cs-json-log-export',daemon=True)
        self.thread.start()

    def _count(self,name):
        with self.lock:
            self.counts[name] += 1

    def __call__(self,record):
        if not _safe_record(record):
            self._count('rejected')
            return
        try:
            line = json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False) + '\n'
            if len(line.encode('utf-8')) > self.max_bytes:
                self._count('rejected')
                return
            if self.stop.is_set():
                self._count('dropped')
                return
            self.queue.put_nowait(line)
        except (Full,ValueError,TypeError):
            self._count('dropped')

    def _consume(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=.05)
            except Empty:
                continue
            try:
                self.writer(line)
                self._count('written')
            except Exception:
                self._count('failed')  # Never log writer errors or payloads.
            finally:
                self.queue.task_done()

    def snapshot(self):
        with self.lock:
            return {**self.counts,'queue_depth':self.queue.qsize(),
                    'worker_alive':self.thread.is_alive()}

    def flush(self,timeout=1):
        deadline = monotonic() + timeout
        while self.queue.unfinished_tasks:
            if monotonic() >= deadline:
                return False
            sleep(.005)
        return True

    def close(self,timeout=1):
        self.stop.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()
