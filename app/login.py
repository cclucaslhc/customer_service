"""Server-side login entry point; no default user or environment credentials."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import ipaddress
import uuid

import sqlalchemy as sa

from app.identity import AuthenticationError, normalize_username
from app.persistence.schema import metadata

LOGIN_LOCK = 1129533503


class LoginRateLimited(RuntimeError):
    def __init__(self, retry_after):
        super().__init__("Login temporarily unavailable; retry later")
        self.retry_after = max(1, min(300, int(retry_after)))


@dataclass(frozen=True)
class LoginPermit:
    slot: int
    owner: uuid.UUID
    expires_at: datetime


class LoginAdmission:
    """Global 60/min, peer 20/min, account 5/5min; two concurrent KDFs.

    Uses database clock/transactions, never sleeps or keeps a transaction open
    over password derivation. Hashes are pseudonyms, not anonymization. Peer must
    be supplied by the server transport, never X-Forwarded-For or the payload.
    Redis/edge limits will supplement this authoritative fail-closed fallback.
    """
    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Shared login admission requires PostgreSQL")
        self.engine = engine

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def acquire(self, username, tenant_id, peer):
        # Passwords belong to globally unique usernames, not memberships. Do
        # not include caller-selected tenant_id: rotating it must not bypass
        # the account budget. Invalid usernames share one bounded bucket.
        try:
            account = f"account:{normalize_username(username)}"
        except (ValueError, TypeError, AttributeError):
            account = "account:invalid"
        if not isinstance(peer, str):
            raise ValueError("Login requires a server-resolved IP peer")
        try:
            address = ipaddress.ip_address(peer)
        except (ValueError, TypeError):
            raise ValueError("Login requires a server-resolved IP peer") from None
        # IPv6 /64 grouping prevents trivial budget bypass by rotating addresses.
        if address.version == 6:
            address = ipaddress.ip_network(f"{address}/64", strict=False)
        factors = (("global", 60, 60), (f"peer:{address}", 20, 60), (account, 5, 300))
        buckets, slots = (metadata.tables[name] for name in ("identity_login_buckets", "identity_login_slots"))
        retry, permit = None, None
        with self.engine.begin() as db:
            db.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOGIN_LOCK})
            now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
            stale = sa.select(buckets.c.scope_hash).where(buckets.c.expires_at <= now).order_by(buckets.c.expires_at).limit(500)
            db.execute(buckets.delete().where(buckets.c.scope_hash.in_(stale)))
            for factor, limit, seconds in factors:
                key = self._hash(factor)
                row = db.execute(sa.select(buckets).where(buckets.c.scope_hash == key)).mappings().first()
                if row and row["attempts"] >= limit:
                    retry = (row["expires_at"] - now).total_seconds() + 1
                    break
                if row:
                    db.execute(buckets.update().where(buckets.c.scope_hash == key).values(attempts=row["attempts"] + 1))
                else:
                    db.execute(buckets.insert().values(scope_hash=key, attempts=1, expires_at=now + timedelta(seconds=seconds)))
            if retry is None:
                # Claim under the shared transaction lock. Row lock also fences
                # login issuance and expired-slot reclamation against each other.
                available = db.execute(sa.select(slots).where(sa.or_(slots.c.owner.is_(None), slots.c.expires_at <= now))
                    .order_by(slots.c.slot).with_for_update(skip_locked=True).limit(1)).mappings().first()
                if available:
                    permit = LoginPermit(available["slot"], uuid.uuid4(), now + timedelta(seconds=120))
                    db.execute(slots.update().where(slots.c.slot == permit.slot).values(owner=permit.owner, expires_at=permit.expires_at))
                else:
                    retry = 1
        # Denial is raised after commit: failed/over-capacity attempts consume
        # budget too; exceptions must not roll back the admission accounting.
        if permit is None:
            raise LoginRateLimited(retry)
        return permit

    @staticmethod
    def require_current(db, permit):
        slots = metadata.tables["identity_login_slots"]
        row = db.execute(sa.select(slots).where(slots.c.slot == permit.slot, slots.c.owner == permit.owner)
            .with_for_update()).mappings().first()
        now = db.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if not row or row["expires_at"] <= now:
            raise AuthenticationError()

    def release(self, permit):
        slots = metadata.tables["identity_login_slots"]
        with self.engine.begin() as db:
            return bool(db.execute(slots.update().where(slots.c.slot == permit.slot, slots.c.owner == permit.owner)
                .values(owner=None, expires_at=None)).rowcount)


class LoginService:
    def __init__(self, identity):
        self.identity = identity
        self.admission = LoginAdmission(identity.engine)

    def login(self, username, password, tenant_id, peer):
        permit = self.admission.acquire(username, tenant_id, peer)
        try:
            return self.identity.login(username, password, tenant_id, admission_permit=permit)
        finally:
            self.admission.release(permit)
