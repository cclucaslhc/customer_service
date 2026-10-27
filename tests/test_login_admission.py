"""Actual shared PostgreSQL admission; synthetic accounts, no HTTP/model calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid

import pytest
pytest.importorskip("sqlalchemy")
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.identity import AuthenticationError
from app.login import LoginAdmission, LoginRateLimited, LoginService
from app.persistence.engine import build_engine
from app.persistence.identity_repository import IdentityRepository
from app.persistence.schema import metadata
from test_postgresql_integration import postgres_database


def test_shared_kdf_capacity_expiry_and_stale_release(postgres_database):
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=4)
    other_engine = build_engine(postgres_database, pool_size=2)
    try:
        gates = [LoginAdmission(engine), LoginAdmission(other_engine)]
        tenant = str(uuid.uuid4())
        def claim(index):
            try:
                return gates[index % 2].acquire(f"synthetic.{index}", tenant, "127.0.0.1")
            except LoginRateLimited as exc:
                assert exc.retry_after == 1
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(8)))
        permits = [result for result in results if result]
        assert len(permits) == 2 and {p.slot for p in permits} == {1, 2}
        slots, buckets = metadata.tables["identity_login_slots"], metadata.tables["identity_login_buckets"]
        with engine.begin() as db:
            global_row = db.execute(sa.select(buckets).where(buckets.c.scope_hash == gates[0]._hash("global"))).mappings().one()
            assert global_row["attempts"] == 8  # Capacity denial commits budgets.
            db.execute(slots.update().where(slots.c.slot == permits[0].slot).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        replacement = gates[1].acquire("synthetic.replacement", tenant, "127.0.0.1")
        assert replacement.slot == permits[0].slot and replacement.owner != permits[0].owner
        assert gates[0].release(permits[0]) is False
        with engine.begin() as db:
            with pytest.raises(AuthenticationError):
                gates[0].require_current(db, permits[0])
            gates[0].require_current(db, replacement)
        assert gates[0].release(replacement)
        assert not gates[1].release(replacement)
        assert gates[1].release(permits[1])
    finally:
        other_engine.dispose()
        engine.dispose()


def test_account_budget_before_kdf_and_lease_fences_session_issuance(postgres_database, monkeypatch):
    import app.persistence.identity_repository as identity_module
    original_verifier = identity_module.verify_password
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=2)
    try:
        identity = IdentityRepository(engine)
        service = LoginService(identity)
        tenant = identity.create_tenant("Synthetic admission")
        identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
        calls = []
        def invalid(*args):
            calls.append(True)
            return False
        monkeypatch.setattr(identity_module, "verify_password", invalid)
        for index in range(5):
            with pytest.raises(AuthenticationError):
                service.login("SYNTHETIC.ADMIN", "synthetic-invalid-password", tenant.upper(), "127.0.0.1")
        with pytest.raises(LoginRateLimited) as exc:
            service.login("synthetic.admin", "synthetic-invalid-password", str(uuid.uuid4()), "127.0.0.2")
        assert 1 <= exc.value.retry_after <= 300 and len(calls) == 5
        slots, buckets = metadata.tables["identity_login_slots"], metadata.tables["identity_login_buckets"]
        with engine.begin() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(slots).where(slots.c.owner.is_not(None))).scalar_one() == 0
            db.execute(buckets.update().values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        def expire_during_kdf(*args):
            with engine.begin() as db:
                db.execute(slots.update().where(slots.c.owner.is_not(None)).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            return True
        monkeypatch.setattr(identity_module, "verify_password", expire_during_kdf)
        with pytest.raises(AuthenticationError):
            service.login("synthetic.admin", "synthetic-password-only", tenant, "127.0.0.1")
        with engine.connect() as db:
            sessions = metadata.tables["identity_sessions"]
            assert db.execute(sa.select(sa.func.count()).select_from(sessions)).scalar_one() == 0
        monkeypatch.setattr(identity_module, "verify_password", original_verifier)
        session = service.login("synthetic.admin", "synthetic-password-only", tenant, "127.0.0.1")
        assert identity.resolve(session.token).tenant_id == tenant
    finally:
        engine.dispose()


def test_peer_ipv6_global_budgets_cleanup_and_fail_closed(postgres_database, monkeypatch):
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        gate = LoginAdmission(engine)
        tenant = str(uuid.uuid4())
        for index in range(20):
            gate.release(gate.acquire(f"synthetic.{index}", tenant, f"2001:db8::{index + 1:x}"))
        with pytest.raises(LoginRateLimited):
            gate.acquire("synthetic.extra", tenant, "2001:db8::ffff")
        for index in range(39):
            gate.release(gate.acquire(f"synthetic.global.{index}", tenant, f"127.1.0.{index + 1}"))
        with pytest.raises(LoginRateLimited):
            gate.acquire("synthetic.global.extra", tenant, "127.2.0.1")
        buckets = metadata.tables["identity_login_buckets"]
        with engine.begin() as db:
            assert db.execute(sa.select(buckets.c.attempts).where(buckets.c.scope_hash == gate._hash("global"))).scalar_one() == 60
            db.execute(buckets.update().values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        gate.release(gate.acquire("synthetic.after.cleanup", tenant, "127.0.0.1"))
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(buckets)).scalar_one() == 3
        for peer in ("attacker.invalid", None, 1, "127.0.0.1, 1.2.3.4"):
            with pytest.raises(ValueError, match="server-resolved"):
                gate.acquire("synthetic.admin", tenant, peer)
        identity = IdentityRepository(engine)
        service = LoginService(identity)
        def no_database(*args, **kwargs):
            raise RuntimeError("Synthetic database unavailable")
        monkeypatch.setattr(engine, "begin", no_database)
        def no_kdf(*args, **kwargs):
            raise AssertionError("Unavailable admission must not reach identity/KDF")
        monkeypatch.setattr(identity, "login", no_kdf)
        with pytest.raises(RuntimeError, match="database unavailable"):
            service.login("synthetic.admin", "synthetic-password-only", tenant, "127.0.0.1")
    finally:
        engine.dispose()
