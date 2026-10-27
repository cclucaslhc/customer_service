"""Credential primitives and deny-by-default tenant authorization.

No environment credential lookup, implicit registration, JWT role claims or
default administrator. HTTP sessions and resource filtering must use this policy.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime


class AuthenticationError(ValueError):
    def __init__(self):
        super().__init__("Invalid credentials or unavailable membership")


class AuthorizationError(PermissionError):
    pass


def normalize_username(value):
    value = value.casefold()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", value):
        raise ValueError("Username must contain 3–64 ASCII account characters")
    return value


def _derive(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=131072, r=8, p=1,
                          maxmem=256 * 1024 * 1024, dklen=32)


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain 12–1024 characters")
    salt = secrets.token_bytes(16)
    return "scrypt-v1$" + salt.hex() + "$" + _derive(password, salt).hex()


def verify_password(password, encoded):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        return False
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt-v1" or len(salt) != 32 or len(expected) != 64:
            return False
        return hmac.compare_digest(_derive(password, bytes.fromhex(salt)), bytes.fromhex(expected))
    except (ValueError, TypeError, AttributeError):
        return False


def token_hash(token):
    if isinstance(token, DelegatedSession):
        return token.digest
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise AuthenticationError()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class DelegatedSession:
    """Worker-only reference to an existing revocable session, not an HTTP token.

    Constructed only from the authorized queue record. Raw digests remain invalid
    Cookie credentials; this object grants no role or tenant claims of its own.
    """
    digest: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.digest, str) or not re.fullmatch(r'[0-9a-f]{64}', self.digest):
            raise AuthenticationError()


PERMISSIONS = {
    "customer": frozenset({"chat.own", "contact.own", "knowledge.read", "model.use", "prompt.use"}),
    "support": frozenset({"chat.own", "chat.tenant", "contact.own", "contact.tenant", "knowledge.read", "model.use", "prompt.use"}),
    "admin": frozenset({"chat.own", "chat.tenant", "contact.own", "contact.tenant", "knowledge.read", "knowledge.manage", "tenant.manage", "audit.read", "model.use", "prompt.use", "config.manage"}),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str

    def require(self, permission):
        if permission not in PERMISSIONS.get(self.role, frozenset()):
            raise AuthorizationError("Permission denied")

    def require_resource(self, tenant_id, owner_id, kind):
        if str(tenant_id) != self.tenant_id:
            raise AuthorizationError("Resource unavailable")
        self.require(f"{kind}.own" if str(owner_id) == self.user_id else f"{kind}.tenant")


@dataclass(frozen=True)
class LoginSession:
    principal: Principal
    expires_at: datetime
    token: str = field(repr=False)
