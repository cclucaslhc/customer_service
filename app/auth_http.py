"""Opt-in cookie authentication boundary; never trusts client roles or proxies.

Business routes are fail-closed until explicitly bound to request services.
This module is not authorization for exposing the local app to the internet.
"""

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import json
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.identity import AuthenticationError, AuthorizationError, normalize_username
from app.login import LoginRateLimited
from starlette.routing import Match
from fastapi.exceptions import RequestValidationError

BOUND_ROUTE_PERMISSIONS = {
    'storage_settings':'config.manage', 'update_storage_settings':'config.manage',
    **{name: "config.manage" for name in ("list_providers", "update_provider", "delete_provider_key", "test_provider", "activate_provider",
        "admin_prompts", "admin_prompt_draft", "admin_prompt_publish", "admin_prompt_rollback", "initialize_tenant",
        "model_budget_status", "model_price_status", "publish_model_price", "publish_model_budget",
        "model_fallback_status", "publish_model_fallback",
        "model_route_budget_status", "publish_model_route_budget")},
    **{name: "chat.tenant" for name in ("admin_sessions", "admin_session_detail", "admin_update_session", "admin_add_session_note",
        "admin_export_session", "admin_overview")},
    "admin_audit": "audit.read", "list_contacts": "contact.tenant", "update_contact": "contact.tenant",
    **{name: "knowledge.manage" for name in ("list_documents", "knowledge_location", "import_knowledge_path", "create_knowledge_job",
        "list_knowledge_jobs", "get_knowledge_job", "cancel_knowledge_job", "upload_document", "delete_document", "reparse_document", "rebuild_index",
        "document_detail", "knowledge_search_test", "knowledge_terms", "update_knowledge_terms")},
    "knowledge_asset": "knowledge.read",
    **{name: "chat.own" for name in ("create_session", "list_sessions", "get_session", "rename_session", "archive_session",
        "restore_session", "delete_session", "send_message", "update_message_feedback", "cancel_run", "follow_run", "run_snapshot")},
    **{name: "chat.tenant" for name in ("admin_archive_session", "admin_restore_session", "admin_delete_session")},
    "create_contact": "contact.own",
}


@dataclass(frozen=True)
class AuthPolicy:
    origin: str
    secure_cookie: bool = True

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or self.origin != f"{parsed.scheme}://{parsed.netloc}"):
            raise ValueError("Authentication needs one canonical explicit origin")
        # Validate port syntax at configuration time.
        parsed.port
        if not self.secure_cookie:
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = parsed.hostname == "localhost"
            if parsed.scheme != "http" or not loopback:
                raise ValueError("Insecure cookies are only allowed for explicit local HTTP testing")
        elif parsed.scheme != "https":
            raise ValueError("Secure authentication requires HTTPS origin")

    @property
    def cookie_name(self):
        return "__Host-cs_session" if self.secure_cookie else "cs_local_session"


def csrf_token(token):
    return hmac.new(token.encode("ascii"), b"cs-csrf:v1", hashlib.sha256).hexdigest()


def _reply(payload, status=200, **headers):
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **headers})


def install_auth(app, repositories, policy):
    router = APIRouter(prefix="/api/auth")

    @app.exception_handler(AuthenticationError)
    async def invalid_identity(request, exc):
        return _reply({"detail": "Authentication required"}, 401)

    @app.exception_handler(AuthorizationError)
    async def denied_identity(request, exc):
        return _reply({"detail": "Permission denied"}, 403)

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(request, exc):
        # Management payloads can contain model keys or contact data. Do not
        # echo Pydantic's input or exception context in HTTP validation errors.
        return _reply({"detail": "Invalid request payload"}, 422)

    @app.middleware("http")
    async def authentication_boundary(request: Request, call_next):
        path = request.url.path
        if path != "/api" and not path.startswith("/api/"):
            return await call_next(request)
        origin = request.headers.get("origin")
        if (origin is not None and origin != policy.origin) or request.headers.get("sec-fetch-site") == "cross-site":
            return _reply({"detail": "Origin denied"}, 403)
        if request.method == "OPTIONS":
            if origin != policy.origin:
                return _reply({"detail": "Origin required"}, 403)
            return await call_next(request)
        if request.method not in {"GET", "HEAD"}:
            if origin != policy.origin:
                return _reply({"detail": "Origin required"}, 403)
        if policy.secure_cookie and request.url.scheme != "https":
            return _reply({"detail": "HTTPS required"}, 403)
        if (path == "/api/auth/login" and request.method == "POST") or (path == "/api/auth/config" and request.method == "GET"):
            return await call_next(request)
        token = request.cookies.get(policy.cookie_name)
        try:
            principal = await asyncio.to_thread(repositories.identity.resolve, token)
        except AuthenticationError:
            return _reply({"detail": "Authentication required"}, 401)
        except Exception:
            return _reply({"detail": "Authentication temporarily unavailable"}, 503)
        if request.method not in {"GET", "HEAD"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), csrf_token(token).encode("ascii")):
                return _reply({"detail": "CSRF validation failed"}, 403)
        request.state.principal = principal
        request.state.login_token = token
        if path not in {"/api/auth/me", "/api/auth/logout"}:
            # Explicit transition barrier. Never authorize the unbound legacy
            # closures merely because the caller has a valid login cookie.
            route = next((route for route in app.router.routes if route.matches(request.scope)[0] == Match.FULL), None)
            permission = BOUND_ROUTE_PERMISSIONS.get(route.name if route else None)
            if permission is None:
                return _reply({"detail": "Authenticated business services not yet enabled"}, 503)
            try:
                principal.require(permission)
            except AuthorizationError:
                return _reply({"detail": "Permission denied"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @router.post("/login")
    async def login(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _reply({"detail": "JSON required"}, 415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                return _reply({"detail": "Login payload too large"}, 413)
        # Do not use default Pydantic HTTP validation errors, which can echo
        # password input. No password/token is logged or included in errors.
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"username", "password", "tenant_id"}:
                raise ValueError()
            username = normalize_username(payload["username"])
            password = payload["password"]
            tenant = str(uuid.UUID(payload["tenant_id"]))
            if not isinstance(password, str) or not 12 <= len(password) <= 1024:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return _reply({"detail": "Invalid login payload"}, 400)
        peer = request.client.host if request.client else None
        try:
            session = await asyncio.to_thread(repositories.login.login, username, password, tenant, peer)
        except AuthenticationError:
            return _reply({"detail": "Invalid credentials or unavailable membership"}, 401)
        except LoginRateLimited as exc:
            return _reply({"detail": "Login temporarily unavailable"}, 429, **{"Retry-After": str(exc.retry_after)})
        except Exception:
            return _reply({"detail": "Authentication temporarily unavailable"}, 503)
        response = _reply({"principal": {"user_id": session.principal.user_id, "tenant_id": session.principal.tenant_id,
            "role": session.principal.role}, "expires_at": session.expires_at.isoformat(), "csrf_token": csrf_token(session.token)})
        response.set_cookie(policy.cookie_name, session.token, max_age=8 * 60 * 60, secure=policy.secure_cookie,
            httponly=True, samesite="strict", path="/")
        return response

    @router.get("/me")
    def me(request: Request):
        principal = request.state.principal
        return _reply({"principal": {"user_id": principal.user_id, "tenant_id": principal.tenant_id, "role": principal.role},
            "csrf_token": csrf_token(request.state.login_token)})

    @router.post("/logout")
    async def logout(request: Request):
        try:
            await asyncio.to_thread(repositories.identity.logout, request.state.login_token)
        except Exception:
            # Do not claim logout succeeded while a durable token remains valid.
            return _reply({"detail": "Logout temporarily unavailable"}, 503)
        response = _reply({"logged_out": True})
        response.delete_cookie(policy.cookie_name, path="/", secure=policy.secure_cookie, httponly=True, samesite="strict")
        return response

    app.include_router(router)
