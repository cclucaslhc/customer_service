"""Provider settings and immutable prompt versions in short atomic transactions."""

import hashlib
import uuid

import sqlalchemy as sa

from app.persistence.base import LocalRepository, Repository, wire
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from sqlalchemy.dialects.postgresql import insert as pg_insert


# Application-scoped advisory lock keys, not derived from secret material.
PROVIDER_LOCK = 1129533501
PROMPT_LOCK = 1129533502


class ConfigurationRepository(Repository):
    table_names = ("provider_configs", "app_settings", "prompt_versions")

    def __init__(self, engine, *, token=None):
        super().__init__(engine)
        if token is not None and engine.dialect.name != "postgresql":
            raise ValueError("Authenticated configuration requires PostgreSQL")
        self._token = token

    def authenticated(self, token):
        from app.persistence.identity_repository import IdentityRepository
        IdentityRepository(self.engine).resolve(token)
        return type(self)(self.engine, token=token)

    def _principal(self, db, permission="config.manage"):
        if self._token is None:
            return None
        from app.persistence.identity_repository import IdentityRepository
        principal = IdentityRepository(self.engine).resolve(self._token, connection=db)
        principal.require(permission)
        return principal

    def _scope(self, db, table, permission="config.manage"):
        principal = self._principal(db, permission)
        if "tenant_id" not in table.c:
            return sa.true()
        return table.c.tenant_id == self.values(table, tenant_id=principal.tenant_id if principal else LEGACY_TENANT_ID)["tenant_id"]

    def _ownership(self, db, table):
        principal = self._principal(db)
        return self.values(table, tenant_id=principal.tenant_id if principal else LEGACY_TENANT_ID) if "tenant_id" in table.c else {}

    def require_manage(self):
        with self.engine.connect() as db:
            self._principal(db)

    def model_costs(self):
        from app.persistence.model_cost_repository import ModelCostRepository
        return ModelCostRepository(self.engine,token=self._token)

    def model_budgets(self):
        from app.persistence.model_budget_repository import ModelBudgetRepository
        return ModelBudgetRepository(self.engine,token=self._token)

    def model_fallbacks(self):
        from app.persistence.model_fallback_repository import ModelFallbackRepository
        return ModelFallbackRepository(self.engine,token=self._token)

    def credential_namespace(self, *, permission="model.use"):
        if self._token is None:
            return None
        with self.engine.connect() as db:
            return self._principal(db, permission).tenant_id

    def initialize_tenant(self, now):
        """Explicit administrator setup; no keys or historical/global fallback."""
        from app.prompting import DEFAULT_BUSINESS_PROMPT
        p, s, prompts = (self.tables[name] for name in self.table_names)
        with self.engine.begin() as db:
            principal = self._principal(db)
            if principal is None:
                raise ValueError("Tenant setup requires authenticated administrator")
            self.serialize_writes(db, PROMPT_LOCK)
            db.execute(pg_insert(p).values(**self.values(p, provider="deepseek", model="deepseek-chat", updated_at=now, key_aad_version=1), **self._ownership(db, p))
                .on_conflict_do_nothing(index_elements=list(p.primary_key.columns)))
            db.execute(pg_insert(s).values(key="active_provider", value="deepseek", **self._ownership(db, s))
                .on_conflict_do_nothing(index_elements=list(s.primary_key.columns)))
            if not db.execute(sa.select(prompts.c.id).where(self._scope(db, prompts))).first():
                draft = self._create_draft(db, content=DEFAULT_BUSINESS_PROMPT, change_note="初始版本", now=now)
                self._publish(db, draft["id"], now)

    def provider_configs(self):
        p, s = self.tables["provider_configs"], self.tables["app_settings"]
        with self.engine.connect() as db:
            active = db.execute(sa.select(s.c.value).where(s.c.key == "active_provider", self._scope(db, s))).scalar_one_or_none() or "deepseek"
            return active, [wire(row) for row in db.execute(sa.select(p).where(self._scope(db, p)).order_by(p.c.provider)).mappings()]

    def provider(self, name):
        p = self.tables["provider_configs"]
        with self.engine.connect() as db:
            return wire(db.execute(sa.select(p).where(p.c.provider == name, self._scope(db, p))).mappings().first())

    def active_provider(self):
        p, s = self.tables["provider_configs"], self.tables["app_settings"]
        with self.engine.connect() as db:
            same_tenant = s.c.tenant_id == p.c.tenant_id if "tenant_id" in p.c else sa.true()
            row = db.execute(sa.select(p).join(s, sa.and_(s.c.key == "active_provider", s.c.value == p.c.provider, same_tenant))
                .where(self._scope(db, p, "model.use"), self._scope(db, s, "model.use"))).mappings().first()
            return wire(row)

    def update_provider(self, name, *, model, now, encrypted_key=None, nonce=None, replace_key=False):
        if replace_key and ((encrypted_key is None) != (nonce is None)):
            raise ValueError("Encrypted key and nonce must be updated together")
        p = self.tables["provider_configs"]
        fields = {"model": model, "updated_at": now}
        if replace_key:
            fields.update(encrypted_key=encrypted_key, nonce=nonce)
        with self.engine.begin() as db:
            scope = self._scope(db, p)
            self.serialize_writes(db, PROVIDER_LOCK)
            if self.engine.dialect.name == "postgresql":
                ownership = self._ownership(db, p)
                if self._token is not None:
                    fields["key_aad_version"] = 1
                elif replace_key:
                    fields["key_aad_version"] = 0
                db.execute(pg_insert(p).values(**self.values(p, provider=name, **fields), **ownership)
                    .on_conflict_do_update(index_elements=list(p.primary_key.columns), set_=self.values(p, **fields)))
                return True
            return bool(db.execute(p.update().where(p.c.provider == name, scope).values(**self.values(p, **fields))).rowcount)

    def delete_provider_key(self, name, now):
        p = self.tables["provider_configs"]
        with self.engine.begin() as db:
            scope = self._scope(db, p)
            self.serialize_writes(db, PROVIDER_LOCK)
            return bool(db.execute(p.update().where(p.c.provider == name, scope).values(**self.values(p, encrypted_key=None, nonce=None, updated_at=now))).rowcount)

    def activate_provider(self, name, *, require_key=True):
        p, s = self.tables["provider_configs"], self.tables["app_settings"]
        with self.engine.begin() as db:
            scope = self._scope(db, p)
            self.serialize_writes(db, PROVIDER_LOCK)
            key = db.execute(sa.select(p.c.encrypted_key).where(p.c.provider == name, scope)).scalar_one_or_none()
            if require_key and not key:
                return False
            if not require_key and not db.execute(sa.select(p.c.provider).where(p.c.provider == name, scope)).first():
                return False
            return bool(db.execute(s.update().where(s.c.key == "active_provider", self._scope(db, s)).values(value=name)).rowcount)

    def record_provider_test(self, name, *, status, message, now):
        p = self.tables["provider_configs"]
        with self.engine.begin() as db:
            db.execute(p.update().where(p.c.provider == name, self._scope(db, p)).values(**self.values(p, last_test_status=status, last_test_message=message, last_test_at=now)))

    def active_prompt(self):
        p = self.tables["prompt_versions"]
        with self.engine.connect() as db:
            return wire(db.execute(sa.select(p).where(p.c.status == "published", self._scope(db, p, "prompt.use")).order_by(p.c.version.desc()).limit(1)).mappings().first())

    def list_prompts(self):
        p = self.tables["prompt_versions"]
        with self.engine.connect() as db:
            return [wire(row) for row in db.execute(sa.select(p).where(self._scope(db, p)).order_by(p.c.version.desc())).mappings()]

    def _create_draft(self, db, *, content, change_note, now):
        p = self.tables["prompt_versions"]
        version = db.execute(sa.select(sa.func.coalesce(sa.func.max(p.c.version), 0) + 1).where(self._scope(db, p))).scalar_one()
        values = self.values(p, id=str(uuid.uuid4()), version=version, content=content.strip(), status="draft",
                             change_note=change_note.strip(), checksum=hashlib.sha256(content.strip().encode("utf-8")).hexdigest(), created_at=now, **self._ownership(db, p))
        db.execute(p.insert().values(**values))
        return wire(db.execute(sa.select(p).where(p.c.id == values["id"])).mappings().one())

    def create_draft(self, *, content, change_note, now):
        with self.engine.begin() as db:
            self._principal(db)
            self.serialize_writes(db, PROMPT_LOCK)
            return self._create_draft(db, content=content, change_note=change_note, now=now)

    def _publish(self, db, prompt_id, now):
        p = self.tables["prompt_versions"]
        key = self.key(p, prompt_id)
        scope = self._scope(db, p)
        target = db.execute(sa.select(p).where(p.c.id == key, scope)).mappings().first()
        if not target:
            return None
        db.execute(p.update().where(p.c.status == "published", scope).values(status="archived"))
        db.execute(p.update().where(p.c.id == key, scope).values(**self.values(p, status="published", published_at=now)))
        return wire(db.execute(sa.select(p).where(p.c.id == key)).mappings().one())

    def publish_prompt(self, prompt_id, now):
        with self.engine.begin() as db:
            self._principal(db)
            self.serialize_writes(db, PROMPT_LOCK)
            return self._publish(db, prompt_id, now)

    def rollback_prompt(self, source_id, change_note, now):
        p = self.tables["prompt_versions"]
        with self.engine.begin() as db:
            scope = self._scope(db, p)
            self.serialize_writes(db, PROMPT_LOCK)
            source = db.execute(sa.select(p).where(p.c.id == self.key(p, source_id), scope)).mappings().first()
            if not source:
                return None
            draft = self._create_draft(db, content=source["content"], change_note=change_note.strip() or f"回滚到 v{source['version']}", now=now)
            return self._publish(db, draft["id"], now)


class LocalConfigurationRepository(LocalRepository):
    repository_type = ConfigurationRepository
