"""Synthetic credentials only; no app import or default secret store."""

import pytest
from app.identity import AuthenticationError, AuthorizationError, LoginSession, Principal, password_hash, token_hash, verify_password


def test_password_hash_salt_policy_and_bounded_format():
    password = "synthetic-password-only"
    first, second = password_hash(password), password_hash(password)
    assert first != second and password not in first
    assert verify_password(password, first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password(password, "scrypt-v1$malformed")
    assert not verify_password(password, "scrypt-v99$" + "00" * 16 + "$" + "00" * 32)
    with pytest.raises(ValueError):
        password_hash("short")
    with pytest.raises(ValueError):
        password_hash("x" * 1025)


def test_principal_denies_unknown_permissions_and_cross_tenant_resources():
    customer = Principal("user-a", "tenant-a", "customer")
    customer.require_resource("tenant-a", "user-a", "chat")
    for args in (("tenant-b", "user-a", "chat"), ("tenant-a", "user-b", "chat"), ("tenant-a", "user-a", "refund")):
        with pytest.raises(AuthorizationError):
            customer.require_resource(*args)
    with pytest.raises(AuthorizationError):
        customer.require("tenant.manage")
    support = Principal("support", "tenant-a", "support")
    support.require_resource("tenant-a", "user-b", "chat")
    with pytest.raises(AuthorizationError):
        support.require("knowledge.manage")
    Principal("admin", "tenant-a", "admin").require("knowledge.manage")
    with pytest.raises(AuthorizationError):
        Principal("unknown", "tenant-a", "unknown").require("chat.own")


def test_tokens_are_bounded_and_redacted_from_session_repr():
    import secrets
    token = secrets.token_urlsafe(32)
    assert len(token_hash(token)) == 64
    assert token not in repr(LoginSession(Principal("u", "t", "customer"), None, token))
    for invalid in ("", "short", "x" * 10000, "é" * 43, None):
        with pytest.raises(AuthenticationError):
            token_hash(invalid)
