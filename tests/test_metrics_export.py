import asyncio
from threading import Event, get_ident
from time import perf_counter

import httpx
import pytest

from app.http_observability import HttpTelemetry
from app.metrics_export import BoundedMetricsExporter, MetricsExportUnavailable, MetricsRenderLimit
from app.private_metrics import MetricsApplication, MetricsPolicy


class BlockedTelemetry(HttpTelemetry):
    def __init__(self):
        super().__init__()
        self.started, self.release = Event(), Event()
        self.calls = 0

    def prometheus(self, **kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(3), 'Synthetic formatter must be released'
        return super().prometheus(**kwargs)


async def wait_finished(exporter):
    deadline = perf_counter() + 2
    while not exporter._future.done():
        assert perf_counter() < deadline
        await asyncio.sleep(.005)


@pytest.mark.asyncio
async def test_blocked_formatter_does_not_block_loop_or_queue_collectors():
    telemetry = BlockedTelemetry()
    exporter = BoundedMetricsExporter(telemetry, timeout=1, max_bytes=4096)
    pending = asyncio.create_task(exporter.render())
    try:
        assert await asyncio.to_thread(telemetry.started.wait, 1)
        assert not pending.done()
        # If render blocked the event loop this phase could not run while it waits.
        await asyncio.wait_for(asyncio.sleep(.01), timeout=.2)
        assert not pending.done() and not telemetry.release.is_set()
        responses = await asyncio.gather(*(exporter.render() for _ in range(30)), return_exceptions=True)
        assert all(isinstance(result, MetricsExportUnavailable) for result in responses)
        assert telemetry.calls == 1 and telemetry.snapshot()['exports']['busy'] == 30
        telemetry.release.set()
        assert b'customer_service_metrics_exports_total' in await pending
        assert telemetry.snapshot()['exports']['success'] == 1
    finally:
        telemetry.release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await exporter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['timeout', 'cancelled'])
async def test_caller_timeout_or_cancel_keeps_real_worker_slot(outcome):
    telemetry = BlockedTelemetry()
    exporter = BoundedMetricsExporter(telemetry, timeout=1 if outcome == 'cancelled' else .1, max_bytes=4096)
    pending = asyncio.create_task(exporter.render())
    try:
        assert await asyncio.to_thread(telemetry.started.wait, 1)
        if outcome == 'cancelled':
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(MetricsExportUnavailable):
                await pending
        assert not exporter._future.done()
        for _ in range(20):
            with pytest.raises(MetricsExportUnavailable):
                await exporter.render()
        assert telemetry.calls == 1
        assert telemetry.snapshot()['exports'][outcome] == 1
        telemetry.release.set()
        await wait_finished(exporter)
        # Expired completion is not reused as a cached successful scrape.
        assert b'customer_service_http_requests_total' in await exporter.render()
        assert telemetry.calls == 2
    finally:
        telemetry.release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await exporter.aclose()


@pytest.mark.asyncio
async def test_size_limit_safe_http_failure_and_recovery_without_stale_cache():
    telemetry = HttpTelemetry()
    for i in range(10):
        telemetry.record(route=f'/registered/{i}',method='GET',status=200,outcome='completed',
                         stream=False,duration=.1,request_id='SECRET_ID')
    token = b'synthetic-export-size-limit-credential-01234567890123456789'
    app = MetricsApplication(telemetry, MetricsPolicy(token=token, max_export_bytes=1024), port=9012)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9012') as client:
            response = await client.get('/metrics',headers={'Authorization':'Bearer '+token.decode()})
            assert response.status_code == 503
            assert 'SECRET_' not in response.text and 'http_requests_total' not in response.text
        assert telemetry.snapshot()['exports']['oversize'] == 1
    finally:
        await app.exporter.aclose()


def test_metric_series_cap_retains_existing_series_and_counts_rejections():
    telemetry = HttpTelemetry(max_series=2)
    def record(route):
        telemetry.record(route=route, method='GET',status=200,outcome='completed',stream=False,
                         duration=.1,request_id='synthetic')
    record('/one'); record('/two'); record('/three'); record('/one')
    snapshot = telemetry.snapshot()
    assert len(snapshot['series']) == 2 and snapshot['dropped_samples'] == 1
    assert sum(row['count'] for row in snapshot['series']) == 3
    assert 'customer_service_http_metric_samples_dropped_total 1' in telemetry.prometheus()
    for cap in (True, 0, 16385, '2'):
        with pytest.raises(ValueError):
            HttpTelemetry(max_series=cap)


def test_cooperative_render_deadline_cancel_and_utf8_byte_budget():
    telemetry = HttpTelemetry()
    telemetry.record(route='/中文模板',method='GET',status=200,outcome='completed',stream=False,
                     duration=.1,request_id='synthetic')
    output = telemetry.prometheus()
    assert telemetry.prometheus(max_bytes=len(output.encode())) == output
    with pytest.raises(MetricsRenderLimit) as failed:
        telemetry.prometheus(max_bytes=len(output.encode())-1)
    assert failed.value.code == 'oversize'
    with pytest.raises(MetricsRenderLimit) as failed:
        telemetry.prometheus(deadline=perf_counter()-1)
    assert failed.value.code == 'deadline'
    stop = Event(); stop.set()
    with pytest.raises(MetricsRenderLimit) as failed:
        telemetry.prometheus(cancel_event=stop)
    assert failed.value.code == 'cancelled'


@pytest.mark.asyncio
async def test_close_stops_cooperative_worker_and_rejects_new_work():
    telemetry = HttpTelemetry()
    exporter = BoundedMetricsExporter(telemetry,timeout=.1,max_bytes=4096)
    assert await exporter.render()
    await exporter.aclose()
    with pytest.raises(MetricsExportUnavailable):
        await exporter.render()
    assert exporter._future.done() and telemetry.snapshot()['exports']['closed'] == 1


@pytest.mark.asyncio
async def test_close_during_render_signals_cancellation_without_queued_replacement():
    class Cooperative(HttpTelemetry):
        def __init__(self):
            super().__init__(); self.started = Event()
        def prometheus(self, **kwargs):
            self.started.set()
            assert kwargs['cancel_event'].wait(1)
            return super().prometheus(**kwargs)
    telemetry = Cooperative()
    exporter = BoundedMetricsExporter(telemetry,timeout=1,max_bytes=4096)
    pending = asyncio.create_task(exporter.render())
    try:
        assert await asyncio.to_thread(telemetry.started.wait,1)
        await exporter.aclose()
        with pytest.raises(MetricsExportUnavailable):
            await pending
        assert exporter._future.done()
        with pytest.raises(MetricsExportUnavailable):
            await exporter.render()
    finally:
        await asyncio.gather(pending, return_exceptions=True)
        await exporter.aclose()


@pytest.mark.asyncio
async def test_formatter_failure_is_safe_and_next_request_is_fresh():
    class Fault(HttpTelemetry):
        fail = True
        def prometheus(self, **kwargs):
            if self.fail:
                raise RuntimeError('SECRET_RENDER_FAILURE')
            return super().prometheus(**kwargs)
    telemetry = Fault()
    exporter = BoundedMetricsExporter(telemetry, timeout=1, max_bytes=4096)
    try:
        with pytest.raises(MetricsExportUnavailable) as failure:
            await exporter.render()
        assert 'SECRET_' not in str(failure.value)
        assert telemetry.snapshot()['exports']['failure'] == 1
        telemetry.fail = False
        assert b'customer_service_metrics_exports_total{outcome="failure"} 1' in await exporter.render()
        assert telemetry.snapshot()['exports']['success'] == 1
    finally:
        await exporter.aclose()


@pytest.mark.asyncio
async def test_bounded_large_snapshot_formats_off_loop_with_correct_terminal_counts():
    class Tracked(HttpTelemetry):
        worker_ident = None
        def prometheus(self, **kwargs):
            self.worker_ident = get_ident()
            return super().prometheus(**kwargs)
    loop_ident = get_ident()
    telemetry = Tracked(max_series=1024)
    for i in range(1200):
        telemetry.record(route=f'/synthetic-template-{i}/{{id}}',method='GET',status=200,
                         outcome='completed',stream=False,duration=.1,request_id='SECRET_ID')
    exporter = BoundedMetricsExporter(telemetry,timeout=5,max_bytes=4*1024*1024)
    ticks = 0
    async def heartbeat():
        nonlocal ticks
        while not formatting.done():
            ticks += 1
            await asyncio.sleep(.001)
    formatting = asyncio.create_task(exporter.render())
    try:
        await heartbeat()
        output = await formatting
        assert ticks > 0 and len(output) <= 4*1024*1024
        assert telemetry.worker_ident is not None and telemetry.worker_ident != loop_ident
        assert output.count(b'customer_service_http_requests_total{') == 1024
        assert b'customer_service_http_metric_samples_dropped_total 176' in output
        assert b'SECRET_ID' not in output
    finally:
        await asyncio.gather(formatting,return_exceptions=True)
        await exporter.aclose()
