"""Internal fencing capability, never accepted as a user-supplied identity."""

from dataclasses import dataclass
import asyncio
import logging
import uuid


class RunLeaseLost(RuntimeError):
    def __init__(self):
        super().__init__('Run lease is no longer current')


class RunCancellationRequested(RunLeaseLost):
    """A current owner observed committed cancellation, not lost ownership."""


@dataclass(frozen=True)
class RunLease:
    run_id: str
    owner: str
    fence: int

    def __post_init__(self):
        uuid.UUID(self.run_id)
        uuid.UUID(self.owner)
        if type(self.fence) is not int or self.fence < 1:
            raise ValueError('Invalid fencing token')


async def run_lease_reaper(repository, stop, *, interval=5):
    """Bounded expiry-only cleanup, independently safe on multiple API instances."""
    logger = logging.getLogger(__name__)
    while not stop.is_set():
        try:
            await asyncio.to_thread(repository.reap_expired_leases)
        except Exception as exc:
            # No connection strings, exception text, SQL parameters or user data.
            logger.warning('run lease cleanup unavailable exception_type=%s', type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
