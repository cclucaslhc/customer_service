"""Tenant-scoped, fail-closed consent for cross-provider online fallback."""

import hashlib
import json
import uuid

import sqlalchemy as sa

from app.persistence.model_cost_repository import CostConflict, ModelCostRepository
from app.persistence.schema import metadata


ONLINE_FALLBACK_PROVIDERS = frozenset({"openai"})
KEY_PREFIX = "online_fallback_consent:"


def _key(provider):
    if provider not in ONLINE_FALLBACK_PROVIDERS:
        raise ValueError("Unsupported online fallback provider")
    return KEY_PREFIX + provider


def _lock(db, tenant_id, provider):
    key = int.from_bytes(hashlib.sha256(f"fallback:{tenant_id}:{provider}".encode()).digest()[:8], "big") & ((1 << 63) - 1)
    db.execute(sa.text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": key})


def _decode(raw):
    if raw is None:
        return {"enabled": False, "version": 0, "publication_id": None}
    try:
        state = json.loads(raw)
        if (type(state) is not dict or set(state) != {"enabled", "version", "publication_id"}
            or type(state.get("enabled")) is not bool
            or type(state.get("version")) is not int or state["version"] < 1
            or not isinstance(state.get("publication_id"), str)):
            raise ValueError
        uuid.UUID(state["publication_id"])
        return state
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("Invalid fallback consent state") from exc


def allows_fallback_attempt(db, *, tenant_id, primary_provider, provider, model):
    """Fail closed at the paid-attempt boundary; never trust an enqueue snapshot."""
    if primary_provider == provider or provider not in (*ONLINE_FALLBACK_PROVIDERS, "local"):
        return False
    _lock(db, tenant_id, provider)
    configs = metadata.tables["provider_configs"]
    configured = db.execute(sa.select(configs.c.model, configs.c.encrypted_key).where(
        configs.c.tenant_id == tenant_id, configs.c.provider == provider)).mappings().first()
    if configured is None or configured["model"] != model:
        return False
    if provider == "local":
        return True  # The gateway separately enforces a fixed loopback endpoint.
    if configured["encrypted_key"] is None:
        return False
    settings = metadata.tables["app_settings"]
    raw = db.execute(sa.select(settings.c.value).where(
        settings.c.tenant_id == tenant_id, settings.c.key == _key(provider))).scalar_one_or_none()
    try:
        return _decode(raw)["enabled"]
    except ValueError:
        return False


class ModelFallbackRepository(ModelCostRepository):
    def status(self, provider):
        key = _key(provider)
        settings = metadata.tables["app_settings"]
        with self.engine.begin() as db:
            principal = self._principal(db)
            tenant = uuid.UUID(principal.tenant_id)
            raw = db.execute(sa.select(settings.c.value).where(
                settings.c.tenant_id == tenant, settings.c.key == key)).scalar_one_or_none()
            try:
                state = _decode(raw)
            except ValueError:
                # Corrupt policy must never be interpreted as consent.
                return {"provider": provider, "enabled": False, "version": None, "state": "invalid"}
            return {"provider": provider, "enabled": state["enabled"],
                    "version": state["version"], "state": "valid"}

    def publish(self, *, provider, enabled, expected_version, publication_id):
        key = _key(provider)
        if type(enabled) is not bool or type(expected_version) is not int or expected_version < 0:
            raise ValueError("Invalid fallback consent")
        publication_id = str(uuid.UUID(str(publication_id)))
        settings = metadata.tables["app_settings"]
        configs = metadata.tables["provider_configs"]
        with self.engine.begin() as db:
            principal = self._principal(db, writing=True)
            tenant = uuid.UUID(principal.tenant_id)
            _lock(db, tenant, provider)
            raw = db.execute(sa.select(settings.c.value).where(
                settings.c.tenant_id == tenant, settings.c.key == key)).scalar_one_or_none()
            state = _decode(raw)
            if state["publication_id"] == publication_id:
                if state["enabled"] != enabled or state["version"] != expected_version + 1:
                    raise CostConflict("Fallback publication identifier conflict")
                return {"provider": provider, "enabled": enabled, "version": state["version"]}
            if state["version"] != expected_version:
                raise CostConflict("Fallback consent version changed")
            if enabled and not db.execute(sa.select(configs.c.provider).where(
                configs.c.tenant_id == tenant, configs.c.provider == provider,
                configs.c.encrypted_key.is_not(None))).first():
                raise ValueError("Online fallback provider is not configured")
            version = expected_version + 1
            value = json.dumps({"enabled": enabled, "version": version,
                                "publication_id": publication_id}, separators=(",", ":"))
            if raw is None:
                db.execute(settings.insert().values(tenant_id=tenant, key=key, value=value))
            else:
                db.execute(settings.update().where(settings.c.tenant_id == tenant,
                    settings.c.key == key).values(value=value))
            db.execute(metadata.tables["audit_logs"].insert().values(
                id=uuid.uuid4(), tenant_id=tenant, actor_id=uuid.UUID(principal.user_id),
                actor=principal.user_id, action="model.fallback.consent.publish",
                entity_type="model_fallback", entity_id=provider,
                summary="Online fallback consent changed",
                metadata_json={"provider": provider, "enabled": enabled, "version": version,
                               "publication_id": publication_id},
                created_at=sa.func.clock_timestamp()))
            return {"provider": provider, "enabled": enabled, "version": version}
