"""Bounded database connections, no implicit schema creation or secret lookup."""

from contextlib import contextmanager
from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine, make_url


def build_engine(url: str, *, pool_size=5, max_overflow=0) -> Engine:
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("Production database requires postgresql+psycopg")
    if pool_size < 1 or max_overflow < 0:
        raise ValueError("Invalid connection pool limits")
    # Libpq's default sslmode=prefer may silently accept cleartext or a server
    # with an unverified identity. Disposable loopback integration databases
    # remain supported; every remote TCP target must verify the server name.
    host = parsed.host
    # Psycopg accepts query-level host/hostaddr and libpq service files which
    # may override the URL hostname. Never grant the loopback exemption when
    # the effective destination can be supplied through those channels.
    destination_override = any(name in parsed.query for name in ("host", "hostaddr", "service", "servicefile"))
    if host not in {"localhost", "127.0.0.1", "::1"} or destination_override:
        if not host or parsed.query.get("sslmode") != "verify-full":
            raise ValueError("Remote PostgreSQL requires sslmode=verify-full")
    connect_args = {"connect_timeout": 10, "options": "-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=5000"}
    if not destination_override and host in {"localhost", "127.0.0.1", "::1"}:
        # Pin the actual socket destination: PGHOSTADDR from the process
        # environment or a modified hosts file must not turn the exception
        # for disposable loopback databases into a remote cleartext channel.
        connect_args["hostaddr"] = "127.0.0.1" if host in {"localhost", "127.0.0.1"} else "::1"
    return sa.create_engine(
        parsed, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=10,
        pool_pre_ping=True, hide_parameters=True,
        connect_args=connect_args,
    )


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """Scope to database operations only; never hold across model/tool HTTP calls."""
    with engine.begin() as connection:
        yield connection
