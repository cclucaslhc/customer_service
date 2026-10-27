"""One owned formatting worker; cancelled/timed-out callers never free its slot."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import perf_counter


class MetricsRenderLimit(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Metrics rendering limit reached')


class MetricsExportUnavailable(ValueError):
    pass


class BoundedMetricsExporter:
    def __init__(self, telemetry, *, timeout, max_bytes):
        self.telemetry, self.timeout, self.max_bytes = telemetry, timeout, max_bytes
        self._executor = None
        self._future = None
        self._stop = Event()
        self._closed = False
        self._loop = None

    def _count(self, outcome):
        recorder = getattr(self.telemetry, 'record_export', None)
        if recorder:
            recorder(outcome)

    def _render(self, deadline):
        return self.telemetry.prometheus(max_bytes=self.max_bytes, deadline=deadline,
                                         cancel_event=self._stop).encode('utf-8')

    async def render(self):
        loop = asyncio.get_running_loop()
        if self._closed:
            self._count('closed')
            raise MetricsExportUnavailable()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise MetricsExportUnavailable()
        if self._future is not None and not self._future.done():
            self._count('busy')
            raise MetricsExportUnavailable()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='cs-metrics-export')
        self._future = self._executor.submit(self._render, perf_counter() + self.timeout)
        result = asyncio.wrap_future(self._future)
        # Shield keeps the underlying slot occupied after requester cancellation;
        # consume late failures without logging raw renderer errors.
        result.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            content = await asyncio.wait_for(asyncio.shield(result), timeout=self.timeout)
            if len(content) > self.max_bytes:
                raise MetricsRenderLimit('oversize')
            self._count('success')
            return content
        except asyncio.CancelledError:
            self._count('cancelled')
            raise
        except asyncio.TimeoutError:
            self._count('timeout')
            raise MetricsExportUnavailable() from None
        except MetricsRenderLimit as exc:
            outcome = 'timeout' if exc.code == 'deadline' else exc.code if exc.code in ('oversize', 'cancelled') else 'failure'
            self._count(outcome)
            raise MetricsExportUnavailable() from None
        except Exception:
            self._count('failure')
            raise MetricsExportUnavailable() from None

    async def aclose(self):
        self._closed = True
        self._stop.set()
        try:
            if self._future is not None and not self._future.done():
                result = asyncio.wrap_future(self._future)
                result.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                try:
                    await asyncio.wait_for(asyncio.shield(result), timeout=self.timeout + .2)
                except Exception:
                    pass  # Cooperative built-in renderer stops; never invent a successful scrape.
        finally:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
