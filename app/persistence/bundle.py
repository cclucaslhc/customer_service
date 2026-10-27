"""Explicit PostgreSQL dependency assembly; never discover databases or model keys."""

import hashlib
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.database import utc_now
from app.persistence.admin_repository import AdminRepository
from app.persistence.chat_repository import ChatRepository
from app.persistence.config_repository import ConfigurationRepository, PROMPT_LOCK
from app.persistence.engine import build_engine
from app.persistence.knowledge_repository import KnowledgeRepository
from app.persistence.identity_repository import IdentityRepository
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.persistence.schema import metadata, SCHEMA_REVISION
from app.prompting import DEFAULT_BUSINESS_PROMPT


class PostgreSQLRepositories:
    """One bounded pool shared by services; migrations are a separate command."""

    def __init__(self, engine, *, strict_role=None, runtime_role='api'):
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL repositories require PostgreSQL")
        if strict_role is not None and type(strict_role) is not bool:
            raise ValueError('Invalid application role policy')
        if runtime_role not in {'api','worker'}:
            raise ValueError('Invalid runtime role kind')
        self.engine = engine
        self.runtime_role = runtime_role
        remote_target = (engine.url.host not in {'localhost','127.0.0.1','::1'}
                         or any(name in engine.url.query for name in ('host','hostaddr','service','servicefile')))
        if remote_target and strict_role is False:
            raise ValueError('Remote PostgreSQL application role check cannot be disabled')
        self.strict_role = remote_target if strict_role is None else strict_role
        self.chat = ChatRepository(engine)
        self.config = ConfigurationRepository(engine)
        self.admin = AdminRepository(engine)
        self.knowledge = KnowledgeRepository(engine)
        self.identity = IdentityRepository(engine)
        from app.login import LoginService
        self.login = LoginService(self.identity)

    @classmethod
    def from_url(cls, url, *, strict_role=None, runtime_role='api', **pool_options):
        engine = build_engine(url, **pool_options)
        try:
            return cls(engine, strict_role=strict_role, runtime_role=runtime_role)
        except Exception:
            engine.dispose()
            raise

    def _check_runtime_role(self):
        if self.strict_role:
            from app.persistence.runtime_role import require_application_role
            with self.engine.begin() as db:
                require_application_role(db,self.runtime_role)

    def initialize(self):
        # No create_all, no implicit migrations, no broad interrupted-run recovery.
        self._check_runtime_role()
        with self.engine.begin() as db:
            version = db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            if version != SCHEMA_REVISION:
                raise RuntimeError("Application schema revision mismatch")
            from app.recovery import require_operational
            require_operational(db)
            self.config.serialize_writes(db, PROMPT_LOCK)
            now = utc_now()
            p, s, prompts = (metadata.tables[name] for name in ("provider_configs", "app_settings", "prompt_versions"))
            for provider, model in (("deepseek", "deepseek-chat"), ("openai", "gpt-5-mini")):
                db.execute(insert(p).values(**self.config.values(p, tenant_id=LEGACY_TENANT_ID, provider=provider, model=model, updated_at=now)).on_conflict_do_nothing(index_elements=list(p.primary_key.columns)))
            db.execute(insert(s).values(**self.config.values(s, tenant_id=LEGACY_TENANT_ID, key="active_provider", value="deepseek")).on_conflict_do_nothing(index_elements=list(s.primary_key.columns)))
            if not db.execute(sa.select(prompts.c.id).where(prompts.c.tenant_id == self.config.values(prompts, tenant_id=LEGACY_TENANT_ID)["tenant_id"]).limit(1)).first():
                db.execute(prompts.insert().values(**self.config.values(prompts, id=str(uuid.uuid4()), version=1,
                    content=DEFAULT_BUSINESS_PROMPT, status="published", change_note="初始版本", checksum=hashlib.sha256(DEFAULT_BUSINESS_PROMPT.encode()).hexdigest(),
                    created_at=now, published_at=now, tenant_id=LEGACY_TENANT_ID)))

    def check_schema(self):
        """Read-only secure startup; no legacy seed or user/config provisioning."""
        self._check_runtime_role()
        with self.engine.connect() as db:
            version = db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            if version != SCHEMA_REVISION:
                raise RuntimeError("Application schema revision mismatch")
            from app.recovery import require_operational
            require_operational(db)

    def health(self):
        with self.engine.connect() as db:
            db.execute(sa.select(sa.literal(1))).scalar_one()
        return {"database_backend": "postgresql", "database_journal_mode": None}

    def close(self):
        self.engine.dispose()
