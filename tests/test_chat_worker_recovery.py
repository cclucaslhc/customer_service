"""Polling recovery must not grant execution authority or replay claimed work."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from app.chat_worker import ChatWorker
from app.chat import ChatOrchestrator
from app.run_leases import RunCancellationRequested


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['reap_expired_leases', 'reap_pending', 'claim'])
@pytest.mark.parametrize('error_type', [OperationalError, InterfaceError])
async def test_database_failure_recovers_at_next_poll_without_skipping_checks(stage,error_type,monkeypatch,caplog):
    worker = object.__new__(ChatWorker)
    worker.log = logging.getLogger('worker-recovery-test')
    stop = asyncio.Event()
    calls = []
    failed = False

    def check(name):
        nonlocal failed
        calls.append(name)
        if name == stage and not failed:
            failed = True
            raise error_type('sensitive-sql', {'secret':'never-log'}, Exception('private-url'))

    worker.queue = SimpleNamespace(reap_expired_leases=lambda:check('reap_expired_leases'),
                                  reap_pending=lambda:check('reap_pending'))

    async def run_once():
        check('claim')
        stop.set()
        return True

    worker.run_once = run_once
    delays = []

    async def wait_for(awaitable,timeout):
        awaitable.close()
        delays.append(timeout)
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio,'wait_for',wait_for)
    await worker.run(stop)
    prefix = ['reap_expired_leases','reap_pending','claim']
    assert calls == prefix[:prefix.index(stage)+1] + prefix
    assert delays == [1]
    assert error_type.__name__ in caplog.text
    assert all(secret not in caplog.text for secret in ('sensitive-sql','never-log','private-url'))


@pytest.mark.asyncio
async def test_programming_error_is_not_hidden_as_transient_database_failure():
    worker = object.__new__(ChatWorker)
    worker.log = logging.getLogger('worker-recovery-test')
    def broken():
        raise ProgrammingError('invalid schema',{},Exception())
    worker.queue = SimpleNamespace(reap_expired_leases=broken)
    worker.run_once = AsyncMock()
    with pytest.raises(ProgrammingError):
        await worker.run(asyncio.Event())
    worker.run_once.assert_not_called()


@pytest.mark.asyncio
async def test_database_backoff_is_capped_and_stop_interrupts_wait(monkeypatch):
    worker = object.__new__(ChatWorker)
    worker.log = logging.getLogger('worker-recovery-test')
    stop = asyncio.Event()
    def broken():
        raise OperationalError('synthetic',{},Exception())
    worker.queue = SimpleNamespace(reap_expired_leases=broken)
    worker.run_once = AsyncMock()
    delays = []
    async def wait_for(awaitable,timeout):
        delays.append(timeout)
        if len(delays) == 8:
            stop.set()
            return await awaitable
        awaitable.close()
        raise asyncio.TimeoutError
    monkeypatch.setattr(asyncio,'wait_for',wait_for)
    await worker.run(stop)
    assert delays == [1,2,4,8,16,30,30,30]
    worker.run_once.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [True,False])
async def test_lease_watcher_distinguishes_committed_cancel_from_lost_authority(cancelled,monkeypatch):
    orchestrator = object.__new__(ChatOrchestrator)
    orchestrator.coordination_check = None
    def renew(*,raise_on_cancel=False):
        assert raise_on_cancel
        if cancelled:
            raise RunCancellationRequested()
        return False
    class Token:
        stopped = False
        def cancel(self): self.stopped = True
    token,state = Token(),{}
    monkeypatch.setattr(asyncio,'sleep',AsyncMock())
    await orchestrator._watch_run_lease(SimpleNamespace(renew_lease=renew),token,state)
    assert token.stopped
    assert state['reason'] == ('cancelled' if cancelled else 'lease_lost')
