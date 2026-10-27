"""Real PostgreSQL verification; creates only cs_test_<uuid> isolated databases."""

import os
import sqlite3
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

import pytest
pytest.importorskip("sqlalchemy", reason="Install production dependencies for PostgreSQL integration")
pytest.importorskip("alembic", reason="Install production dependencies for migrations")
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.persistence.engine import build_engine
from app.persistence.chat_repository import ChatRepository
from app.persistence.admin_repository import AdminRepository
from app.persistence.knowledge_repository import KnowledgeRepository, KnowledgeWriteConflict
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.config_repository import ConfigurationRepository
from app.persistence.legacy_import import import_snapshot
from app.persistence.schema_v1 import metadata
from app.persistence.schema import metadata as current_metadata
from test_production_migration import snapshot


@pytest.fixture
def postgres_database(monkeypatch):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is unset; real PostgreSQL verification remains outstanding")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("Integration database must use an explicit local endpoint")
    admin = build_engine(url, pool_size=1)
    name = "cs_test_" + uuid.uuid4().hex
    created = False
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        database_url = parsed.set(database=name).render_as_string(hide_password=False)
        vector_engine = build_engine(database_url, pool_size=1)
        try:
            with vector_engine.begin() as connection:
                connection.exec_driver_sql('CREATE EXTENSION vector')
        except sa.exc.DBAPIError as exc:
            pytest.fail(f'Isolated PostgreSQL must provide pgvector before migrations: {type(exc).__name__}')
        finally:
            vector_engine.dispose()
        monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
        yield database_url
    finally:
        if created:
            # Exact generated target, never drop the administrator-selected database.
            assert name.startswith("cs_test_") and len(name) == 40
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_real_upgrade_import_replay_sequences_and_schema(snapshot, postgres_database):
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("INSERT INTO app_settings(key,value) VALUES('knowledge_synonyms',?)", ('[["synthetic","evidence"]]',))
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        report = import_snapshot(snapshot, engine)
        assert report["status"] == "imported"
        assert import_snapshot(snapshot, engine)["status"] == "already_imported"
        inspector = sa.inspect(engine)
        assert set(inspector.get_table_names()) == set(current_metadata.tables) | {"alembic_version"}
        knowledge = KnowledgeRepository(engine)
        assert knowledge.setting("knowledge_synonyms") == '[["synthetic","evidence"]]'
        assert knowledge.setting("active_provider") is None
        with engine.begin() as connection:
            settings = current_metadata.tables["knowledge_settings"]
            connection.execute(settings.update().values(value="tampered synthetic metadata"))
        from app.persistence.legacy_import import ImportValidationError
        with pytest.raises(ImportValidationError, match="knowledge settings"):
            import_snapshot(snapshot, engine)
        with engine.begin() as connection:
            chunks = metadata.tables["knowledge_chunks"]
            document = connection.execute(sa.select(metadata.tables["documents"].c.id)).scalar_one()
            new_id = connection.execute(chunks.insert().values(
                document_id=document, chunk_index=1, content="next", tokenized="next"
            ).returning(chunks.c.id)).scalar_one()
            assert new_id == 43
        assert not inspector.get_view_names()  # Legacy FTS5 was not imported.
    finally:
        engine.dispose()


def test_real_message_order_import_same_timestamp_and_replay(snapshot, postgres_database):
    # UUID order deliberately opposes SQLite insertion order. No user data.
    first = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    second = "00000000-0000-0000-0000-000000000001"
    with closing(sqlite3.connect(snapshot)) as source, source:
        source.execute("UPDATE messages SET id=?", (first,))
        source.execute("INSERT INTO messages(id,session_id,role,content,created_at) "
                       "SELECT ?,session_id,'assistant','Synthetic answer',created_at FROM messages", (second,))
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=2)
    try:
        report = import_snapshot(snapshot, engine)
        assert report["message_order"]["count"] == 2
        repo = ChatRepository(engine)
        with engine.connect() as db:
            sid = str(db.execute(sa.select(metadata.tables["chat_sessions"].c.id)).scalar_one())
        history = repo.history(sid)
        assert [row["id"] for row in history] == [first, second]
        assert [row["sequence"] for row in history] == [1, 2]
        assert repo.list_sessions()[0]["last_message"] == "Synthetic answer"
        assert [row["id"] for row in AdminRepository(engine).detail(sid)["messages"]] == [first, second]
        assert import_snapshot(snapshot, engine)["status"] == "already_imported"
        # Content hashes alone do not prove the ordering invariant.
        with engine.begin() as db:
            db.execute(current_metadata.tables["messages"].update().values(sequence=current_metadata.tables["messages"].c.sequence + 10))
        from app.persistence.legacy_import import ImportValidationError
        with pytest.raises(ImportValidationError, match="message order"):
            import_snapshot(snapshot, engine)
    finally:
        engine.dispose()


def test_real_import_replay_checks_legacy_credential_format(snapshot, postgres_database):
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        import_snapshot(snapshot, engine)
        with engine.begin() as db:
            providers = current_metadata.tables["provider_configs"]
            db.execute(providers.update().values(key_aad_version=1))
        from app.persistence.legacy_import import ImportValidationError
        with pytest.raises(ImportValidationError, match="credential format"):
            import_snapshot(snapshot, engine)
    finally:
        engine.dispose()


def test_real_configuration_upgrade_preserves_legacy_cipher_without_key_access(postgres_database, tmp_path, monkeypatch):
    from app.persistence.identity_repository import IdentityRepository
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    from app.security import SecretStore
    config = Config("alembic.ini")
    command.upgrade(config, "0006_knowledge_tenants")
    engine = build_engine(postgres_database, pool_size=2)
    try:
        now = datetime.now(timezone.utc)
        # Synthetic ciphertext is produced only in an explicit test directory.
        store = SecretStore(path=tmp_path / "synthetic-master.key")
        ciphertext, nonce = store.encrypt("synthetic-legacy-only", "deepseek")
        prompt_id = uuid.uuid4()
        with engine.begin() as db:
            db.execute(metadata.tables["provider_configs"].insert().values(provider="deepseek", model="legacy-synthetic",
                encrypted_key=ciphertext, nonce=nonce, updated_at=now))
            db.execute(metadata.tables["app_settings"].insert().values(key="active_provider", value="deepseek"))
            db.execute(metadata.tables["prompt_versions"].insert().values(id=prompt_id, version=1,
                content="Synthetic legacy policy", status="published", checksum="synthetic", created_at=now, published_at=now))
        def forbidden(*args, **kwargs):
            raise AssertionError("Migration must never access master keys")
        with monkeypatch.context() as patch:
            patch.setattr(SecretStore, "_get_key", forbidden)
            command.upgrade(config, "head")
            command.upgrade(config, "head")
        legacy = ConfigurationRepository(engine)
        saved = legacy.active_provider()
        assert saved["tenant_id"] == LEGACY_TENANT_ID and saved["key_aad_version"] == 0
        assert saved["encrypted_key"] == ciphertext and saved["nonce"] == nonce
        assert store.decrypt(saved["encrypted_key"], saved["nonce"], "deepseek") == "synthetic-legacy-only"
        assert legacy.active_prompt()["id"] == str(prompt_id)
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant("Synthetic new tenant")
        identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
        token = identity.login("synthetic.admin", "synthetic-password-only", tenant).token
        scoped = legacy.authenticated(token)
        assert scoped.active_provider() is None and scoped.active_prompt() is None
        assert scoped.provider_configs()[1] == []
        scoped.initialize_tenant(now.isoformat())
        row = scoped.provider("deepseek")
        assert row["encrypted_key"] is None and row["nonce"] is None
        assert row["model"] != "legacy-synthetic"
        assert scoped.active_prompt()["version"] == 1 and scoped.active_prompt()["id"] != str(prompt_id)
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
            providers = current_metadata.tables["provider_configs"]
            db.execute(providers.update().where(providers.c.tenant_id == uuid.UUID(tenant)).values(
                encrypted_key=ciphertext, nonce=nonce, key_aad_version=0))
        with pytest.raises(ValueError, match="Legacy quarantine"):
            identity.provision_user("synthetic.legacy", "synthetic-password-only", LEGACY_TENANT_ID, role="admin")
    finally:
        engine.dispose()


def test_real_message_ordinals_concurrency_clock_skew_and_rollback(postgres_database):
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=4)
    try:
        repo = ChatRepository(engine)
        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())
        later, earlier = "2026-10-07T10:00:00+00:00", "2026-10-07T09:00:00+00:00"
        repo.start_run(session_id=sid, run_id=rid, user_id=str(uuid.uuid4()), title="Synthetic", content="Question",
                       provider="deepseek", model="synthetic", prompt_version_id=None, now=later)
        with pytest.raises(sa.exc.IntegrityError):
            repo.complete_run(run_id=rid, session_id=sid, message_id=str(uuid.uuid4()), content=None,
                              provider="deepseek", model="synthetic", citations=[], now=earlier)
        assert repo.complete_run(run_id=rid, session_id=sid, message_id=str(uuid.uuid4()), content="Answer",
                                 provider="deepseek", model="synthetic", citations=[], now=earlier)
        assert [row["content"] for row in repo.history(sid)] == ["Question", "Answer"]
        assert [row["sequence"] for row in repo.history(sid)] == [1, 2]
        assert repo.list_sessions()[0]["last_message"] == "Answer"
        def start(index):
            return repo.start_run(session_id=sid, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Synthetic",
                content=f"Question {index}", provider="deepseek", model="synthetic", prompt_version_id=None, now=earlier)
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(pool.map(start, range(12)))
        assert [row["sequence"] for row in repo.history(sid)] == list(range(1, 15))
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
            db.execute(current_metadata.tables["messages"].insert().values(id=uuid.uuid4(), session_id=uuid.UUID(sid),
                sequence=1, role="user", content="duplicate", created_at=datetime.fromisoformat(later)))
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
            db.execute(current_metadata.tables["messages"].insert().values(id=uuid.uuid4(), session_id=uuid.UUID(sid),
                sequence=0, role="user", content="invalid", created_at=datetime.fromisoformat(later)))
        assert len(repo.history(sid)) == 14
    finally:
        engine.dispose()


def test_real_baseline_upgrade_preserves_prior_order_and_blocks_old_app(postgres_database):
    config = Config("alembic.ini")
    command.upgrade(config, "0001_legacy_baseline")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=1)
    try:
        with pytest.raises(RuntimeError, match="revision mismatch"):
            bundle.initialize()
        now, sid = datetime.now(timezone.utc), uuid.uuid4()
        with bundle.engine.begin() as db:
            db.execute(metadata.tables["chat_sessions"].insert().values(id=sid, title="Synthetic", created_at=now, updated_at=now))
            db.execute(metadata.tables["messages"].insert(), [dict(id=uuid.UUID(value), session_id=sid, role="user", content="Synthetic", created_at=now)
                for value in ("ffffffff-ffff-ffff-ffff-ffffffffffff", "00000000-0000-0000-0000-000000000001")])
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        bundle.initialize()
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        with bundle.engine.connect() as db:
            assert compare_metadata(MigrationContext.configure(db), current_metadata) == []
        assert [row["sequence"] for row in bundle.chat.history(str(sid))] == [1, 2]
        assert bundle.chat.history(str(sid))[0]["id"] == "00000000-0000-0000-0000-000000000001"
        # The revision deliberately forbids losing persistent order on downgrade.
        with pytest.raises(RuntimeError, match="downgrade is disabled"):
            command.downgrade(config, "0001_legacy_baseline")
    finally:
        bundle.close()


def test_real_identity_login_revocation_tenant_policy_and_expiry(postgres_database):
    from app.identity import AuthenticationError, AuthorizationError, token_hash
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=2)
    try:
        repo = IdentityRepository(engine)
        tenant = repo.create_tenant("Synthetic tenant")
        other = repo.create_tenant("Unrelated synthetic tenant")
        password = "synthetic-password-only"
        uid = repo.provision_user("Synthetic.User", password, tenant)
        session = repo.login("SYNTHETIC.USER", password, tenant)
        principal = repo.resolve(session.token)
        assert principal.user_id == uid and principal.role == "customer"
        principal.require_resource(tenant, uid, "chat")
        with pytest.raises(AuthorizationError):
            principal.require_resource(other, uid, "chat")
        with pytest.raises(AuthorizationError):
            principal.require_resource(tenant, str(uuid.uuid4()), "chat")
        for username, supplied, selected in (("synthetic.user", "wrong-password-only", tenant),
            ("synthetic.user", password, other), ("unknown.user", password, tenant)):
            with pytest.raises(AuthenticationError):
                repo.login(username, supplied, selected)
        s = current_metadata.tables["identity_sessions"]
        with engine.connect() as db:
            stored = db.execute(sa.select(s)).mappings().one()
            assert stored["token_hash"] == token_hash(session.token)
            assert session.token not in repr(stored)
            assert db.execute(sa.select(sa.func.count()).select_from(current_metadata.tables["identity_events"])).scalar_one() == 2
        repo.update_membership(tenant, uid, role="support", active=True)
        with pytest.raises(AuthenticationError):
            repo.resolve(session.token)
        support = repo.login("synthetic.user", password, tenant)
        repo.resolve(support.token).require_resource(tenant, str(uuid.uuid4()), "chat")
        assert repo.logout(support.token)
        assert not repo.logout(support.token)
        with pytest.raises(AuthenticationError):
            repo.resolve(support.token)
        expired = repo.login("synthetic.user", password, tenant)
        with engine.begin() as db:
            db.execute(s.update().where(s.c.token_hash == token_hash(expired.token)).values(
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc), expires_at=datetime(2020, 1, 2, tzinfo=timezone.utc)))
        with pytest.raises(AuthenticationError):
            repo.resolve(expired.token)
        active = repo.login("synthetic.user", password, tenant)
        repo.set_tenant_active(tenant, False)
        repo.set_tenant_active(tenant, True)
        with pytest.raises(AuthenticationError):
            repo.resolve(active.token)
        active = repo.login("synthetic.user", password, tenant)
        repo.set_user_active(uid, False)
        repo.set_user_active(uid, True)
        with pytest.raises(AuthenticationError):
            repo.resolve(active.token)
    finally:
        engine.dispose()


def test_real_identity_password_reset_race_and_transactional_events(postgres_database, monkeypatch):
    import threading
    import app.persistence.identity_repository as identity_module
    from app.identity import AuthenticationError
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=2)
    try:
        repo = identity_module.IdentityRepository(engine)
        tenant = repo.create_tenant("Synthetic tenant")
        password = "synthetic-old-password"
        uid = repo.provision_user("synthetic.account", password, tenant)
        previous = repo.login("synthetic.account", password, tenant)
        verified, proceed = threading.Event(), threading.Event()
        verifier = identity_module.verify_password
        def delayed_verification(*args):
            result = verifier(*args)
            verified.set()
            assert proceed.wait(10)
            return result
        monkeypatch.setattr(identity_module, "verify_password", delayed_verification)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(repo.login, "synthetic.account", password, tenant)
            try:
                assert verified.wait(10)
                repo.reset_password(uid, "synthetic-new-password")
            finally:
                proceed.set()
            with pytest.raises(AuthenticationError):
                future.result(timeout=10)
        with pytest.raises(AuthenticationError):
            repo.resolve(previous.token)
        monkeypatch.setattr(identity_module, "verify_password", verifier)
        assert repo.resolve(repo.login("synthetic.account", "synthetic-new-password", tenant).token).user_id == uid
        def broken_event(*args):
            raise RuntimeError("Synthetic audit outage")
        monkeypatch.setattr(repo, "_event", broken_event)
        with pytest.raises(RuntimeError, match="audit outage"):
            repo.provision_user("must.rollback", "synthetic-password-only", tenant)
        with engine.connect() as db:
            users = current_metadata.tables["identity_users"]
            assert db.execute(sa.select(sa.func.count()).select_from(users).where(users.c.username == "must.rollback")).scalar_one() == 0
        sessions = current_metadata.tables["identity_sessions"]
        with engine.connect() as db:
            before = db.execute(sa.select(sa.func.count()).select_from(sessions)).scalar_one()
        with pytest.raises(RuntimeError, match="audit outage"):
            repo.login("synthetic.account", "synthetic-new-password", tenant)
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(sessions)).scalar_one() == before
    finally:
        engine.dispose()


def test_real_authenticated_chat_isolation_all_operations_and_revocation(postgres_database):
    from app.identity import AuthenticationError, AuthorizationError
    from app.persistence.identity_repository import IdentityRepository
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    command.upgrade(Config("alembic.ini"), "head")
    # A single connection also proves authorization does not acquire a second
    # pooled connection inside repository transactions.
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw = IdentityRepository(engine), ChatRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        password = "synthetic-password-only"
        users, repos, tokens = {}, {}, {}
        for name, selected, role in (("customer.a", tenant, "customer"), ("customer.b", tenant, "customer"),
            ("customer.c", other, "customer"), ("support.a", tenant, "support")):
            users[name] = identity.provision_user(name, password, selected, role=role)
            tokens[name] = identity.login(name, password, selected).token
            repos[name] = raw.authenticated(tokens[name])
        sessions, runs, answers, tools = {}, {}, {}, {}
        now = datetime.now(timezone.utc).isoformat()
        for name in ("customer.a", "customer.b", "customer.c"):
            repo = repos[name]
            sessions[name], runs[name], answers[name] = (str(uuid.uuid4()) for _ in range(3))
            assert repo.start_run(session_id=sessions[name], run_id=runs[name], user_id=str(uuid.uuid4()), title=name,
                content="Synthetic question", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
            tools[name] = repo.start_tool(runs[name], "synthetic-read-tool", now)
            assert repo.finish_tool(tools[name], now)
            assert repo.complete_run(run_id=runs[name], session_id=sessions[name], message_id=answers[name], content="Synthetic answer",
                provider="deepseek", model="synthetic", citations=[], now=now)
            assert repo.session(sessions[name])["owner_id"] == users[name]
            # Leave a second run/tool active so denial cannot pass merely because
            # the target is already terminal.
            runs[name] = str(uuid.uuid4())
            assert repo.start_run(session_id=sessions[name], run_id=runs[name], user_id=str(uuid.uuid4()), title=name,
                content="Synthetic next question", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
            tools[name] = repo.start_tool(runs[name], "synthetic-pending-tool", now)
        mine, support = repos["customer.a"], repos["support.a"]
        assert [row["id"] for row in mine.list_sessions()] == [sessions["customer.a"]]
        assert {row["id"] for row in support.list_sessions(admin=True)} == {sessions["customer.a"], sessions["customer.b"]}
        with pytest.raises(AuthorizationError):
            mine.list_sessions(admin=True)
        for foreign in ("customer.b", "customer.c"):
            sid, rid, aid, tool = sessions[foreign], runs[foreign], answers[foreign], tools[foreign]
            assert mine.session(sid, include_deleted=True) is None
            assert mine.history(sid) == []
            assert not mine.update_session(sid, now=now, title="forged")
            assert not mine.feedback(sid, aid, "down")
            assert not mine.start_run(session_id=sid, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="forged",
                content="forged", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
            assert not mine.complete_run(run_id=rid, session_id=sid, message_id=str(uuid.uuid4()), content="forged",
                provider="deepseek", model="synthetic", citations=[], now=now)
            assert not mine.finish_run(rid, "error", "forged", now)
            assert mine.start_tool(rid, "forged-tool", now) is None
            assert not mine.finish_tool(tool, now)
            assert raw.session(sid) is None and raw.history(sid) == []
            with engine.connect() as db:
                r, t = current_metadata.tables["agent_runs"], current_metadata.tables["agent_run_tools"]
                assert db.execute(sa.select(r.c.status).where(r.c.id == uuid.UUID(rid))).scalar_one() == "running"
                assert db.execute(sa.select(t.c.finished_at).where(t.c.id == tool)).scalar_one() is None
        assert support.history(sessions["customer.c"]) == []
        assert mine.feedback(sessions["customer.a"], answers["customer.a"], "up")
        assert support.update_session(sessions["customer.b"], now=now, management_status="resolved")
        with pytest.raises(ValueError, match="Unsupported"):
            mine.update_session(sessions["customer.a"], now=now, owner_id=users["customer.b"])
        with pytest.raises(AuthorizationError):
            mine.update_session(sessions["customer.a"], now=now, assignee="forged")
        legacy_sid = str(uuid.uuid4())
        assert raw.start_run(session_id=legacy_sid, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Legacy synthetic",
            content="restricted legacy", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
        assert raw.session(legacy_sid)["tenant_id"] == LEGACY_TENANT_ID
        assert mine.session(legacy_sid) is None and support.session(legacy_sid) is None
        with pytest.raises(ValueError, match="cannot be activated"):
            identity.set_tenant_active(LEGACY_TENANT_ID, True)
        shared_id = str(uuid.uuid4())
        def collide(name):
            return repos[name].start_run(session_id=shared_id, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Concurrent synthetic",
                content="Concurrent synthetic", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(collide, ("customer.a", "customer.c")))
        assert sum(winners) == 1
        for name, won in zip(("customer.a", "customer.c"), winners):
            assert bool(repos[name].session(shared_id)) is won
            assert bool(repos[name].history(shared_id)) is won
        identity.logout(tokens["customer.a"])
        with pytest.raises(AuthenticationError):
            mine.history(sessions["customer.a"])
        with pytest.raises(AuthenticationError):
            mine.start_run(session_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="forged",
                content="forged", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
    finally:
        engine.dispose()


def test_real_chat_composite_constraints_and_old_identity_upgrade(postgres_database):
    from app.persistence.identity_repository import IdentityRepository
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    command.upgrade(Config("alembic.ini"), "0003_identity")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        now, legacy = datetime.now(timezone.utc), uuid.uuid4()
        with engine.begin() as db:
            db.execute(metadata.tables["chat_sessions"].insert().values(id=legacy, title="Old synthetic", created_at=now, updated_at=now))
        command.upgrade(Config("alembic.ini"), "head")
        repo, raw = IdentityRepository(engine), ChatRepository(engine)
        assert raw.session(str(legacy))["tenant_id"] == LEGACY_TENANT_ID
        assert raw.session(str(legacy))["owner_id"] is None
        with engine.connect() as db:
            assert db.execute(sa.select(current_metadata.tables["identity_tenants"].c.active).where(
                current_metadata.tables["identity_tenants"].c.id == uuid.UUID(LEGACY_TENANT_ID))).scalar_one() is False
        tenant, other = repo.create_tenant("Synthetic A"), repo.create_tenant("Synthetic B")
        uid = repo.provision_user("synthetic.owner", "synthetic-password-only", tenant)
        bound = raw.authenticated(repo.login("synthetic.owner", "synthetic-password-only", tenant).token)
        sid, rid = str(uuid.uuid4()), str(uuid.uuid4())
        assert bound.start_run(session_id=sid, run_id=rid, user_id=str(uuid.uuid4()), title="Synthetic",
            content="Synthetic", provider="deepseek", model="synthetic", prompt_version_id=None, now=now.isoformat())
        invalid = [
            ("messages", dict(id=uuid.uuid4(), session_id=uuid.UUID(sid), tenant_id=uuid.UUID(other), sequence=2, role="user", content="forged", created_at=now)),
            ("agent_runs", dict(id=uuid.uuid4(), session_id=uuid.UUID(sid), tenant_id=uuid.UUID(other), status="running", started_at=now)),
            ("agent_run_tools", dict(run_id=uuid.UUID(rid), tenant_id=uuid.UUID(other), tool_name="forged", status="running", started_at=now)),
            ("chat_sessions", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(other), owner_id=uuid.UUID(uid), created_at=now, updated_at=now)),
            ("chat_sessions", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant), created_at=now, updated_at=now)),
        ]
        for name, values in invalid:
            with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
                db.execute(current_metadata.tables[name].insert().values(**values))
        assert len(bound.history(sid)) == 1
    finally:
        engine.dispose()


def test_real_admin_contacts_notes_audit_scope_and_roles(postgres_database):
    from app.identity import AuthorizationError, AuthenticationError
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw = IdentityRepository(engine), AdminRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        password = "synthetic-password-only"
        ids, tokens, repos = {}, {}, {}
        for name, selected, role in (("customer.a", tenant, "customer"), ("support.a", tenant, "support"),
            ("admin.a", tenant, "admin"), ("admin.b", other, "admin")):
            ids[name] = identity.provision_user(name, password, selected, role=role)
            tokens[name] = identity.login(name, password, selected).token
            repos[name] = raw.authenticated(tokens[name])
        now, sessions = datetime.now(timezone.utc).isoformat(), {}
        for name in ("customer.a", "admin.b"):
            sessions[name] = str(uuid.uuid4())
            assert repos[name].start_run(session_id=sessions[name], run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()),
                title=name, content="Synthetic", provider="deepseek", model="synthetic", prompt_version_id=None, now=now)
        customer, support, manager, foreign = (repos[name] for name in ("customer.a", "support.a", "admin.a", "admin.b"))
        cid = customer.create_contact(session_id=sessions["customer.a"], name="Synthetic", phone=None, email=None, summary="Synthetic contact A", now=now)
        other_cid = foreign.create_contact(session_id=sessions["admin.b"], name="Synthetic", phone=None, email=None, summary="Synthetic contact B", now=now)
        assert manager.managed_sessions({}, 1, 10)["total"] == 1
        assert support.managed_sessions({"q": "admin.b"}, 1, 10)["total"] == 0
        assert support.detail(sessions["admin.b"]) is None
        assert support.add_note(sessions["admin.b"], "forged", now) is None
        assert support.create_contact(session_id=sessions["admin.b"], name="Synthetic", phone=None, email=None, summary="forged", now=now) is None
        assert customer.create_contact(session_id=sessions["admin.b"], name="Synthetic", phone=None, email=None, summary="forged", now=now) is None
        assert manager.update_contact(other_cid, {"status": "closed"}) is None
        assert support.list_contacts(None, None, 1, 10)["total"] == 1
        assert support.list_contacts("contact B", None, 1, 10)["total"] == 0
        for operation in (lambda: customer.detail(sessions["customer.a"]), lambda: customer.managed_sessions({}, 1, 10),
            lambda: customer.add_note(sessions["customer.a"], "forged", now), lambda: customer.overview(),
            lambda: customer.list_contacts(None, None, 1, 10), lambda: customer.update_contact(cid, {"status": "closed"}),
            lambda: customer.list_audit(1, 10), lambda: support.list_audit(1, 10)):
            with pytest.raises(AuthorizationError):
                operation()
        note = support.add_note(sessions["customer.a"], "Synthetic private note", now)
        assert note["author_id"] == ids["support.a"]
        assert manager.detail(sessions["customer.a"])["notes"][0]["content"] == "Synthetic private note"
        changed = support.update_contact(cid, {"status": "following", "note": "Synthetic internal followup"})
        assert changed["owner_id"] == ids["customer.a"]
        delegated = support.create_contact(session_id=sessions["customer.a"], name="Synthetic", phone=None, email=None, summary="Synthetic delegated", now=now)
        assert next(row for row in manager.list_contacts(None, None, 1, 10)["items"] if row["id"] == delegated)["owner_id"] == ids["customer.a"]
        audits = manager.list_audit(1, 100)["items"]
        assert all(row["tenant_id"] == tenant for row in audits)
        assert any(row["action"] == "contact.create" and row["actor_id"] == ids["customer.a"] for row in audits)
        assert any(row["action"] == "contact.update" and row["actor_id"] == ids["support.a"] for row in audits)
        assert len([row for row in audits if row["action"] == "contact.update"]) == 1
        assert all("Synthetic private note" not in repr(row) and "Synthetic internal followup" not in repr(row) for row in audits)
        overview = manager.overview()
        assert overview["sessions"] == overview["runs"] == 1 and overview["contacts"] == 2
        assert overview["providers"] == [] and overview["documents"] == 0  # Not an unscoped/global count.
        assert raw.managed_sessions({}, 1, 10)["total"] == raw.list_contacts(None, None, 1, 10)["total"] == 0
        assert raw.detail(sessions["customer.a"]) is None and raw.list_audit(1, 10)["total"] == 0
        identity.logout(tokens["support.a"])
        with pytest.raises(AuthenticationError):
            support.list_contacts(None, None, 1, 10)
    finally:
        engine.dispose()


def test_real_admin_atomic_audit_and_composite_owner_constraints(postgres_database, monkeypatch):
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        uid = identity.provision_user("admin.a", "synthetic-password-only", tenant, role="admin")
        same_tenant_other = identity.provision_user("customer.a", "synthetic-password-only", tenant)
        other_uid = identity.provision_user("admin.b", "synthetic-password-only", other, role="admin")
        repo = AdminRepository(engine).authenticated(identity.login("admin.a", "synthetic-password-only", tenant).token)
        now, sid = datetime.now(timezone.utc), str(uuid.uuid4())
        repo.start_run(session_id=sid, run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Synthetic", content="Synthetic",
            provider="deepseek", model="synthetic", prompt_version_id=None, now=now.isoformat())
        cid = repo.create_contact(session_id=sid, name="Synthetic", phone=None, email=None, summary="Synthetic", now=now.isoformat())
        def unavailable(*args, **kwargs):
            raise RuntimeError("Synthetic audit outage")
        monkeypatch.setattr(repo, "_write_audit", unavailable)
        with pytest.raises(RuntimeError, match="audit outage"):
            repo.update_contact(cid, {"status": "closed"})
        with pytest.raises(RuntimeError, match="audit outage"):
            repo.create_contact(session_id=None, name="Synthetic", phone=None, email=None, summary="Synthetic", now=now.isoformat())
        with pytest.raises(RuntimeError, match="audit outage"):
            repo.add_note(sid, "Synthetic", now.isoformat())
        assert repo.list_contacts(None, None, 1, 10)["total"] == 1
        assert repo.list_contacts(None, None, 1, 10)["items"][0]["status"] == "new"
        assert repo.detail(sid)["notes"] == []
        invalid = [
            ("contacts", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant), owner_id=uuid.UUID(same_tenant_other), session_id=uuid.UUID(sid), name="Synthetic", summary="forged", created_at=now)),
            ("contacts", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(other), owner_id=uuid.UUID(other_uid), session_id=uuid.UUID(sid), name="Synthetic", summary="forged", created_at=now)),
            ("session_notes", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(other), author_id=uuid.UUID(other_uid), session_id=uuid.UUID(sid), content="forged", created_at=now)),
            ("audit_logs", dict(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant), actor_id=uuid.UUID(other_uid), action="forged", entity_type="test", summary="forged", created_at=now)),
        ]
        for name, values in invalid:
            with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
                db.execute(current_metadata.tables[name].insert().values(**values))
    finally:
        engine.dispose()


def test_real_admin_upgrade_keeps_parent_ownership_and_unknown_historical_authors(postgres_database):
    from app.persistence.identity_repository import IdentityRepository
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    command.upgrade(Config("alembic.ini"), "0004_chat_tenants")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw_chat = IdentityRepository(engine), ChatRepository(engine)
        tenant = identity.create_tenant("Synthetic tenant")
        uid = identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
        token = identity.login("synthetic.admin", "synthetic-password-only", tenant).token
        now = datetime.now(timezone.utc)
        sessions = [str(uuid.uuid4()), str(uuid.uuid4())]
        # Seed historical schema directly: current repositories require the
        # current migration head and must not run against a partially upgraded DB.
        historical = sa.MetaData()
        historical.reflect(engine, only=("chat_sessions",))
        with engine.begin() as db:
            for sid, selected, owner in ((sessions[0], tenant, uid), (sessions[1], LEGACY_TENANT_ID, None)):
                db.execute(historical.tables["chat_sessions"].insert().values(
                    id=uuid.UUID(sid), tenant_id=uuid.UUID(selected), owner_id=uuid.UUID(owner) if owner else None,
                    title="Synthetic", status="idle", created_at=now, updated_at=now))
        with engine.begin() as db:
            for sid in sessions:
                db.execute(metadata.tables["contacts"].insert().values(id=uuid.uuid4(), session_id=uuid.UUID(sid), name="Synthetic", summary="Historical synthetic", created_at=now))
                db.execute(metadata.tables["session_notes"].insert().values(id=uuid.uuid4(), session_id=uuid.UUID(sid), content="Historical synthetic note", created_at=now))
            db.execute(metadata.tables["contacts"].insert().values(id=uuid.uuid4(), name="Synthetic", summary="Unassigned historical", created_at=now))
        command.upgrade(Config("alembic.ini"), "head")
        manager, legacy = AdminRepository(engine).authenticated(token), AdminRepository(engine)
        assert manager.list_contacts(None, None, 1, 10)["total"] == 1
        assert manager.list_contacts(None, None, 1, 10)["items"][0]["owner_id"] == uid
        assert manager.detail(sessions[0])["notes"][0]["author_id"] is None
        assert manager.detail(sessions[0])["notes"][0]["tenant_id"] == tenant
        assert legacy.list_contacts(None, None, 1, 10)["total"] == 2
        assert all(row["tenant_id"] == LEGACY_TENANT_ID and row["owner_id"] is None for row in legacy.list_contacts(None, None, 1, 10)["items"])
    finally:
        engine.dispose()


def test_real_knowledge_isolation_search_fallback_assets_files_and_roles(postgres_database, tmp_path, monkeypatch):
    from app.identity import AuthorizationError, AuthenticationError
    from app.persistence.identity_repository import IdentityRepository
    from app.knowledge import KnowledgeService, KnowledgeError, KnowledgeDuplicateError
    import app.knowledge as knowledge_module
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw = IdentityRepository(engine), KnowledgeRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        password, tokens, repos = "synthetic-password-only", {}, {}
        for name, selected, role in (("admin.a", tenant, "admin"), ("admin.b", other, "admin"), ("customer.a", tenant, "customer")):
            identity.provision_user(name, password, selected, role=role)
            tokens[name] = identity.login(name, password, selected).token
            repos[name] = raw.authenticated(tokens[name])
        def service(repo):
            return KnowledgeService(repository=repo, knowledge_dir=tmp_path / "documents", asset_dir=tmp_path / "assets", legacy_upload_dir=tmp_path / "legacy")
        a, b, customer = (service(repos[name]) for name in ("admin.a", "admin.b", "customer.a"))
        body = b"Synthetic shared lexical evidence"
        first, second = a.ingest("synthetic.txt", body), b.ingest("synthetic.txt", body)
        assert first["id"] != second["id"]
        assert repos["admin.a"].document(first["id"])["sha256"] == repos["admin.b"].document(second["id"])["sha256"]
        assert a.knowledge_dir == tmp_path / "documents" / tenant
        assert b.knowledge_dir == tmp_path / "documents" / other
        with pytest.raises(KnowledgeDuplicateError):
            a.ingest("duplicate.txt", body)
        assert raw.list_documents() == []
        assert repos["admin.a"].document(second["id"]) is None
        assert repos["admin.a"].document_chunks(second["id"]) == []
        assert not repos["admin.a"].delete_document(second["id"], 1)
        assert {row["id"] for row in a.list_documents()} == {first["id"]}
        for terms in (["lexical"], ["no-lexical-hit"]):
            lexical, fallback = repos["customer.a"].search_candidates(terms, 10)
            assert {row["document_id"] for row in lexical + fallback} <= {first["id"]}
            assert fallback
        assert {row["document_id"] for row in customer.search("lexical evidence")} == {first["id"]}
        assert raw.search_candidates(["lexical"], 10) == ([], [])
        for repo, result, svc in ((repos["admin.a"], first, a), (repos["admin.b"], second, b)):
            document = repo.document(result["id"])
            for key in ("tenant_id", "created_by"):
                document.pop(key)
            document.update(version=2, chunk_count=1)
            aid = str(uuid.uuid4())
            from PIL import Image
            svc.asset_dir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1, 1), "white").save(svc.asset_dir / "synthetic.png")
            png = (svc.asset_dir / "synthetic.png").read_bytes()
            import hashlib
            asset = dict(id=aid, document_id=result["id"], asset_index=0, stored_name="synthetic.png", media_type="image/png",
                size=len(png), width=1, height=1, location="Synthetic", alt_text="Synthetic", sha256=hashlib.sha256(png).hexdigest(),
                metadata_json={}, created_at=datetime.now(timezone.utc).isoformat())
            repo.publish_document(document, [dict(document_id=result["id"], chunk_index=0, content=body.decode(), tokenized="synthetic lexical evidence")], [asset], expected_version=1)
            result["asset_id"] = aid
        assert customer.asset_file(first["asset_id"])[0] == a.asset_dir / "synthetic.png"
        assert repos["customer.a"].asset(second["asset_id"]) is None
        assert repos["customer.a"].assets([second["id"]]) == []
        with pytest.raises(KnowledgeError):
            customer.asset_file(second["asset_id"])
        foreign_path = b.knowledge_dir / repos["admin.b"].document(second["id"])["stored_name"]
        with pytest.raises(KnowledgeError, match="租户"):
            a.ingest("forged.txt", b"Synthetic new", source_path=foreign_path)
        with pytest.raises(KnowledgeError, match="租户"):
            a._import_candidates(str(foreign_path))
        def forbidden_parser(*args, **kwargs):
            raise AssertionError("Denied callers must not reach parsing or OCR")
        monkeypatch.setattr(knowledge_module, "parse_structured_document", forbidden_parser)
        with pytest.raises(AuthorizationError):
            customer.ingest("denied.txt", b"Synthetic")
        for operation in (lambda: repos["customer.a"].list_documents(), lambda: repos["customer.a"].document(first["id"]),
            lambda: repos["customer.a"].document_chunks(first["id"]), lambda: customer.delete(first["id"]),
            lambda: customer.reparse(first["id"]), lambda: repos["customer.a"].set_setting("knowledge_synonyms", "[]")):
            with pytest.raises(AuthorizationError):
                operation()
        identity.logout(tokens["customer.a"])
        with pytest.raises(AuthenticationError):
            customer.search("lexical")
        with pytest.raises(AuthenticationError):
            customer.asset_file(first["asset_id"])
    finally:
        engine.dispose()


def test_real_knowledge_settings_jobs_rebuild_and_tenant_constraints(postgres_database):
    from app.identity import AuthorizationError
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw = IdentityRepository(engine), KnowledgeRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        repos = []
        for name, selected in (("admin.a", tenant), ("admin.b", other)):
            identity.provision_user(name, "synthetic-password-only", selected, role="admin")
            repos.append(raw.authenticated(identity.login(name, "synthetic-password-only", selected).token))
        a, b = repos
        now, ids = datetime.now(timezone.utc).isoformat(), []
        for repo in repos:
            did = str(uuid.uuid4())
            ids.append(did)
            doc = dict(id=did, display_name="Synthetic", stored_name="Synthetic.txt", extension=".txt", size=10, sha256="a" * 64,
                status="ready", chunk_count=1, version=1, source_path="same-synthetic-source", created_at=now)
            repo.publish_document(doc, [dict(document_id=did, chunk_index=0, content="Synthetic", tokenized="Synthetic")], [])
        assert a.lookup_ingest("same-synthetic-source", "a" * 64)[0]["id"] == ids[0]
        assert b.lookup_ingest("same-synthetic-source", "a" * 64)[0]["id"] == ids[1]
        a.set_setting("knowledge_synonyms", '[ ["alpha", "synthetic"] ]')
        assert b.setting("knowledge_synonyms") is None and raw.setting("knowledge_synonyms") is None
        b.set_setting("knowledge_synonyms", '[ ["beta", "synthetic"] ]')
        assert a.setting("knowledge_synonyms") != b.setting("knowledge_synonyms")
        assert a.rebuild(lambda value: "tenant-a-token", "synthetic-a-version") == 1
        assert b.setting("knowledge_tokenizer_version") is None
        with engine.connect() as db:
            chunks = current_metadata.tables["knowledge_chunks"]
            assert db.execute(sa.select(chunks.c.tokenized).where(chunks.c.document_id == uuid.UUID(ids[1]))).scalar_one() == "Synthetic"
        job = str(uuid.uuid4())
        a.create_job(dict(id=job, source_path="Synthetic", recursive=True, status="pending", created_at=now))
        assert a.job(job)["tenant_id"] == tenant and a.job(job)["requested_by"]
        assert b.job(job) is None and b.jobs(10) == [] and raw.jobs(10) == []
        assert not b.update_job(job, {"status": "error"})
        assert a.job(job)["status"] == "pending"
        assert a.update_job(job, {"status": "running"})
        with pytest.raises(ValueError, match="server controlled"):
            a.create_job(dict(id=str(uuid.uuid4()), tenant_id=other, source_path="Synthetic", status="pending", created_at=now))
        with pytest.raises(ValueError, match="server controlled"):
            a.publish_document({"tenant_id": other}, [], [])
        with pytest.raises(KnowledgeWriteConflict, match="unavailable"):
            doc = b.document(ids[1])
            doc.pop("tenant_id"); doc.pop("created_by")
            a.publish_document({**doc, "version": 2}, [dict(document_id=ids[1], chunk_index=0, content="forged", tokenized="forged")], [], expected_version=1)
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as db:
            db.execute(current_metadata.tables["knowledge_chunks"].insert().values(tenant_id=uuid.UUID(other), document_id=uuid.UUID(ids[0]),
                chunk_index=1, content="forged", tokenized="forged"))
        assert a.delete_document(ids[0], 1)
        assert b.document(ids[1]) is not None
    finally:
        engine.dispose()


def test_real_knowledge_upgrade_copies_only_legacy_knowledge_settings(postgres_database):
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "0005_admin_tenants")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        with engine.begin() as db:
            db.execute(metadata.tables["app_settings"].insert(), [
                {"key": "knowledge_synonyms", "value": '[["synthetic","evidence"]]'},
                {"key": "active_provider", "value": "deepseek"},
                {"key": "knowledgeOther", "value": "not a knowledge setting"}])
        command.upgrade(Config("alembic.ini"), "head")
        raw, identity = KnowledgeRepository(engine), IdentityRepository(engine)
        assert raw.setting("knowledge_synonyms") == '[["synthetic","evidence"]]'
        assert raw.setting("active_provider") is None and raw.setting("knowledgeOther") is None
        tenant = identity.create_tenant("Synthetic")
        identity.provision_user("synthetic.admin", "synthetic-password-only", tenant, role="admin")
        bound = raw.authenticated(identity.login("synthetic.admin", "synthetic-password-only", tenant).token)
        assert bound.setting("knowledge_synonyms") is None  # No global fallback.
    finally:
        engine.dispose()


def test_real_tenant_gateway_configuration_aad_roles_and_revocation(postgres_database, tmp_path, monkeypatch):
    from app.identity import AuthorizationError, AuthenticationError
    from app.persistence.identity_repository import IdentityRepository
    from app.providers import ModelGateway, ProviderError
    from app.security import SecretStore
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity, raw = IdentityRepository(engine), ConfigurationRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        repos, tokens = {}, {}
        for name, selected, role in (("admin.a", tenant, "admin"), ("admin.b", other, "admin"), ("customer.a", tenant, "customer")):
            identity.provision_user(name, "synthetic-password-only", selected, role=role)
            tokens[name] = identity.login(name, "synthetic-password-only", selected).token
            repos[name] = raw.authenticated(tokens[name])
        store = SecretStore(path=tmp_path / "synthetic-master.key")
        gateways = {name: ModelGateway(store, repository=repo) for name, repo in repos.items()}
        a, b, customer = (gateways[name] for name in ("admin.a", "admin.b", "customer.a"))
        def forbidden_client(*args, **kwargs):
            raise AssertionError("No real model HTTP client may be used")
        for gateway in gateways.values():
            monkeypatch.setattr(gateway, "_client", forbidden_client)
        for name in ("admin.a", "admin.b"):
            repos[name].initialize_tenant(datetime.now(timezone.utc).isoformat())
            repos[name].initialize_tenant(datetime.now(timezone.utc).isoformat())
            assert [row["provider"] for row in gateways[name].list_configs()["providers"]] == ["deepseek", "openai", "local"]
        a.update("local", "synthetic-local-a", None)
        assert repos["admin.a"].provider("local")["model"] == "synthetic-local-a"
        assert repos["admin.b"].provider("local") is None
        assert next(row for row in b.list_configs()["providers"] if row["provider"] == "local")["configured"] is False
        with pytest.raises(ProviderError, match="本机模型服务未配置"):
            a.activate("local")
        assert repos["admin.a"].provider_configs()[0] == "deepseek"
        with pytest.raises(AuthorizationError):
            customer.update("deepseek", "forged", "synthetic-denied-key")
        assert not store.path.exists()  # Denied before encryption/provisioning.
        a.update("deepseek", "synthetic-model-a", "synthetic-key-a-only")
        b.update("deepseek", "synthetic-model-b", "synthetic-key-b-only")
        assert a.snapshot().api_key == customer.snapshot().api_key == "synthetic-key-a-only"
        assert b.snapshot().api_key == "synthetic-key-b-only"
        assert a.snapshot().tenant_id == tenant and b.snapshot().tenant_id == other
        assert "synthetic-key-a-only" not in repr(a.snapshot())
        assert raw.provider_configs()[1] == [] and raw.active_provider() is None
        for operation in (customer.list_configs, lambda: customer.delete_key("deepseek"), lambda: customer.activate("deepseek"),
            lambda: repos["customer.a"].provider("deepseek"), lambda: repos["customer.a"].initialize_tenant(datetime.now(timezone.utc).isoformat())):
            with pytest.raises(AuthorizationError):
                operation()
        with pytest.raises(ProviderError, match="租户"):
            a.build_model(b.snapshot())
        assert a.activate("deepseek") is None
        assert repos["admin.a"].activate_provider("openai") is False
        a.delete_key("deepseek")
        with pytest.raises(ProviderError, match="配置"):
            a.snapshot()
        assert b.snapshot().api_key == "synthetic-key-b-only"
        a.update("deepseek", "synthetic-model-a", "synthetic-key-a-only")
        # Simulate database-level ciphertext transplant; the metadata version is
        # preserved, but AES-GCM binding still rejects the other tenant's bytes.
        stolen = repos["admin.a"].provider("deepseek")
        with engine.begin() as db:
            p = current_metadata.tables["provider_configs"]
            db.execute(p.update().where(p.c.tenant_id == uuid.UUID(other), p.c.provider == "deepseek")
                .values(encrypted_key=stolen["encrypted_key"], nonce=stolen["nonce"]))
        with pytest.raises(ProviderError, match="解密"):
            b.snapshot()
        assert a.snapshot().api_key == "synthetic-key-a-only"
        identity.logout(tokens["customer.a"])
        with pytest.raises(AuthenticationError):
            customer.snapshot()
    finally:
        engine.dispose()


def test_real_prompt_tenant_versions_concurrency_publish_and_run_fk(postgres_database):
    from app.identity import AuthorizationError
    from app.persistence.identity_repository import IdentityRepository
    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=4)
    try:
        identity, raw = IdentityRepository(engine), ConfigurationRepository(engine)
        tenant, other = identity.create_tenant("Synthetic A"), identity.create_tenant("Synthetic B")
        tokens, repos = {}, {}
        for name, selected, role in (("admin.a", tenant, "admin"), ("admin.b", other, "admin"), ("customer.a", tenant, "customer")):
            identity.provision_user(name, "synthetic-password-only", selected, role=role)
            tokens[name] = identity.login(name, "synthetic-password-only", selected).token
            repos[name] = raw.authenticated(tokens[name])
        now = datetime.now(timezone.utc).isoformat()
        a, b, customer = (repos[name] for name in ("admin.a", "admin.b", "customer.a"))
        a.initialize_tenant(now); b.initialize_tenant(now)
        assert a.active_prompt()["version"] == b.active_prompt()["version"] == 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            drafts = list(pool.map(lambda index: a.create_draft(content=f"Synthetic A {index}", change_note="Synthetic", now=now), range(8)))
        assert sorted(row["version"] for row in drafts) == list(range(2, 10))
        foreign = b.create_draft(content="Synthetic B private policy", change_note="Synthetic", now=now)
        b.publish_prompt(foreign["id"], now)
        assert a.publish_prompt(foreign["id"], now) is None
        assert a.rollback_prompt(foreign["id"], "forged", now) is None
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda draft: a.publish_prompt(draft["id"], now), drafts))
        assert len([row for row in a.list_prompts() if row["status"] == "published"]) == 1
        assert b.active_prompt()["id"] == foreign["id"]
        restored = a.rollback_prompt(drafts[0]["id"], "Synthetic rollback", now)
        assert restored["version"] == 10 and restored["id"] != drafts[0]["id"]
        assert customer.active_prompt()["id"] == restored["id"]
        assert raw.active_prompt() is None and raw.list_prompts() == []
        for operation in (customer.list_prompts, lambda: customer.create_draft(content="forged", change_note="", now=now),
            lambda: customer.publish_prompt(restored["id"], now)):
            with pytest.raises(AuthorizationError):
                operation()
        chat = ChatRepository(engine).authenticated(tokens["customer.a"])
        with pytest.raises(sa.exc.IntegrityError):
            chat.start_run(session_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Synthetic", content="Synthetic",
                provider="deepseek", model="synthetic", prompt_version_id=foreign["id"], now=now)
        assert chat.list_sessions() == []  # Whole failed start transaction rolled back.
        assert chat.start_run(session_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), title="Synthetic", content="Synthetic",
            provider="deepseek", model="synthetic", prompt_version_id=restored["id"], now=now)
    finally:
        engine.dispose()


def test_real_postgres_http_application_has_no_sqlite_or_real_secrets(postgres_database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pydantic_ai.models.function import FunctionModel
    import app.main as main_module
    from app.persistence.base import LocalRepository
    from app.providers import ProviderSnapshot
    from app.security import SecretStore

    command.upgrade(Config("alembic.ini"), "head")
    bundle = PostgreSQLRepositories.from_url(postgres_database, pool_size=3)
    def forbidden(*args, **kwargs):
        raise AssertionError("PostgreSQL application must never fall back to SQLite")
    monkeypatch.setattr(LocalRepository, "_get", forbidden)
    monkeypatch.setattr(main_module, "init_database", forbidden)
    monkeypatch.setattr(main_module, "recover_interrupted_runs", forbidden)
    monkeypatch.setattr(main_module, "local_database_health", forbidden)
    application = main_module.create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path / "secrets" / "master.key"),
        knowledge_paths={"knowledge_dir": tmp_path / "knowledge", "asset_dir": tmp_path / "assets", "legacy_upload_dir": tmp_path / "legacy"})

    async def stream(messages, info):
        yield "Synthetic integration response."
    class Gateway:
        def snapshot(self):
            return ProviderSnapshot("deepseek", "synthetic-function-model", "unused")
        def build_model(self, snapshot):
            return FunctionModel(stream_function=stream)
    application.state.chat.gateway = Gateway()
    try:
        with TestClient(application) as client:
            assert client.get("/health").json()["database_backend"] == "postgresql"
            assert client.get("/health").json()["master_key"] is False
            assert client.get("/health/ready").status_code == 200
            configuration = client.get("/api/admin/providers").json()
            assert configuration["active_provider"] == "deepseek"
            assert not any(row["configured"] for row in configuration["providers"])
            saved = client.put("/api/admin/providers/deepseek", json={"model": "synthetic-model", "api_key": "synthetic-not-a-model-key"})
            assert saved.status_code == 200 and "synthetic-not-a-model-key" not in saved.text
            assert (tmp_path / "secrets" / "master.key").exists()
            sid = client.post("/api/chat/sessions").json()["id"]
            events = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Hello"})
            assert "event: done" in events.text
            history = client.get(f"/api/chat/sessions/{sid}").json()["messages"]
            assert [row["role"] for row in history] == ["user", "assistant"]
            assert history[1]["content"] == "Synthetic integration response."
            note = client.post(f"/api/admin/chat/sessions/{sid}/notes", json={"content": "Synthetic note"})
            assert note.status_code == 200
            assert client.get(f"/api/admin/chat/sessions/{sid}").json()["notes"][0]["content"] == "Synthetic note"
            original = client.get("/api/admin/prompts").json()["current"]
            draft = client.post("/api/admin/prompts/drafts", json={"content": original["content"] + "\nSynthetic change."}).json()
            assert client.post(f"/api/admin/prompts/{draft['id']}/publish").status_code == 200
            uploaded = client.post("/api/admin/documents", files={"file": ("synthetic.txt", b"Synthetic lexical evidence for isolated integration.", "text/plain")})
            assert uploaded.status_code == 200
            did = uploaded.json()["id"]
            assert client.get(f"/api/admin/documents/{did}").json()["document"]["display_name"] == "synthetic.txt"
            hits = client.post("/api/admin/knowledge/search-test", json={"query": "lexical"}).json()["items"]
            assert hits[0]["document_id"] == did and hits[0]["score_kind"] == "postgres_ts_rank"
            terms = client.put("/api/admin/knowledge/terms", json={"groups": [["synthetic", "example"]]})
            assert terms.status_code == 200
            assert client.get("/api/admin/knowledge/terms").json()["custom"] == [["synthetic", "example"]]
            contact = client.post("/api/contacts", json={"session_id": sid, "name": "Synthetic", "email": "test@example.com", "summary": "Test"})
            assert contact.status_code == 200
            assert client.get("/api/admin/contacts").json()["total"] == 1
            assert client.get("/api/admin/overview").json()["sessions"] == 1
            assert client.get("/api/admin/audit").json()["total"] > 0
        bundle.initialize()  # Idempotent startup must not reset saved settings.
        assert bundle.config.provider("deepseek")["model"] == "synthetic-model"
        assert bundle.config.active_prompt()["id"] == draft["id"]
    finally:
        bundle.close()


def test_real_chat_and_configuration_transactions(snapshot, postgres_database):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone

    command.upgrade(Config("alembic.ini"), "head")
    engine = build_engine(postgres_database, pool_size=4)
    try:
        import_snapshot(snapshot, engine)
        chat = ChatRepository(engine)
        config = ConfigurationRepository(engine)
        now = datetime.now(timezone.utc).isoformat()
        session_id, run_id, user_id = (str(uuid.uuid4()) for _ in range(3))
        assert chat.start_run(session_id=session_id, run_id=run_id, user_id=user_id, title="synthetic",
                              content="Question", provider="deepseek", model="test", prompt_version_id=config.active_prompt()["id"], now=now)
        assert chat.complete_run(run_id=run_id, session_id=session_id, message_id=str(uuid.uuid4()), content="Answer",
                                 provider="deepseek", model="test", citations=[{"synthetic": True}],
                                 now=datetime.now(timezone.utc).isoformat())
        assert [row["content"] for row in chat.history(session_id)] == ["Question", "Answer"]
        assert chat.session(session_id)["status"] == "idle"
        assert not chat.finish_run(run_id, "error", "late", now)
        assert not config.activate_provider("openai")
        config.update_provider("deepseek", model="test", now=now, encrypted_key=b"synthetic", nonce=b"nonce", replace_key=True)
        assert config.activate_provider("deepseek")
        assert config.active_provider()["encrypted_key"] == b"synthetic"

        with ThreadPoolExecutor(max_workers=4) as executor:
            drafts = list(executor.map(lambda i: config.create_draft(content=f"Synthetic {i}", change_note="test", now=now), range(8)))
            list(executor.map(lambda row: config.publish_prompt(row["id"], now), drafts))
        assert len({row["version"] for row in drafts}) == 8
        assert sum(row["status"] == "published" for row in config.list_prompts()) == 1
        restored = config.rollback_prompt(drafts[0]["id"], "", now)
        assert restored["id"] != drafts[0]["id"]
        assert restored["status"] == "published"

        shared_session = str(uuid.uuid4())
        concurrent_runs = [str(uuid.uuid4()) for _ in range(8)]
        def start_concurrent(rid):
            return chat.start_run(session_id=shared_session, run_id=rid, user_id=str(uuid.uuid4()), title="concurrent",
                                  content="Synthetic", provider="deepseek", model="test", prompt_version_id=None, now=now)
        with ThreadPoolExecutor(max_workers=4) as executor:
            assert all(executor.map(start_concurrent, concurrent_runs))
            assert all(executor.map(lambda rid: chat.finish_run(rid, "interrupted", "test", now), concurrent_runs))
        assert len(chat.history(shared_session)) == 8
        assert chat.session(shared_session)["status"] == "idle"
        administrative = AdminRepository(engine)
        administrative.update_session(shared_session, now=now, tags_json=["synthetic%tag"])
        found = administrative.managed_sessions({"tag": "synthetic%tag"}, 1, 10)
        assert found["total"] == 1 and found["items"][0]["id"] == shared_session
        assert administrative.managed_sessions({"tag": "synthetic"}, 1, 10)["total"] == 0
        administrative.add_note(shared_session, "synthetic note", now)
        administrative.audit(action="session.synthetic", entity_type="session", entity_id=shared_session, summary="test", metadata={"synthetic": True}, now=now)
        cid = administrative.create_contact(session_id=shared_session, name="Synthetic", phone=None, email="test@synthetic.invalid", summary="100% synthetic", now=now)
        assert administrative.update_contact(cid, {"status": "following", "note": "test"})["status"] == "following"
        assert administrative.list_contacts("100%", "following", 1, 10)["total"] == 1
        detail = administrative.detail(shared_session)
        assert detail["notes"][0]["content"] == "synthetic note"
        assert any(row["metadata_json"] == {"synthetic": True} for row in detail["audit"])
        assert administrative.overview()["contacts"] == 1
        knowledge = KnowledgeRepository(engine)
        doc_id = str(uuid.uuid4())
        doc = {"id": doc_id, "display_name": "Synthetic", "stored_name": "synthetic.txt", "extension": ".txt", "size": 10,
               "sha256": "b" * 64, "status": "ready", "chunk_count": 1, "version": 1, "source_path": "synthetic-source", "created_at": now,
               "metadata_json": {"synthetic": True}, "warnings_json": []}
        chunks = [{"document_id": doc_id, "chunk_index": 0, "content": "synthetic lexical evidence", "tokenized": "synthetic lexical evidence", "metadata_json": {"page": 1}}]
        knowledge.publish_document(doc, chunks, [])
        assert knowledge.document(doc_id)["metadata_json"] == {"synthetic": True}
        lexical, _ = knowledge.search_candidates(["lexical"], 10)
        assert lexical[0]["document_id"] == doc_id and lexical[0]["score_kind"] == "postgres_ts_rank"
        with pytest.raises(KnowledgeWriteConflict):
            knowledge.publish_document({**doc, "version": 2}, chunks, [], expected_version=0)
        knowledge.publish_document({**doc, "version": 2}, chunks, [], expected_version=1)
        snapshots = current_metadata.tables['knowledge_chunk_versions']
        with engine.connect() as db:
            versions = db.execute(sa.select(snapshots.c.document_version, snapshots.c.content_sha256,
                snapshots.c.parser_version, snapshots.c.chunker_version)
                .where(snapshots.c.document_id == uuid.UUID(doc_id))
                .order_by(snapshots.c.document_version)).all()
        import hashlib
        assert [row.document_version for row in versions] == [1, 2]
        assert all(row.content_sha256 == hashlib.sha256(chunks[0]['content'].encode()).hexdigest()
                   and row.parser_version and row.chunker_version for row in versions)
        knowledge.set_setting("synthetic-test", "value")
        assert knowledge.setting("synthetic-test") == "value"
        jid = str(uuid.uuid4())
        knowledge.create_job({"id": jid, "source_path": "synthetic", "recursive": True, "status": "pending", "created_at": now})
        knowledge.update_job(jid, {"result_json": {"synthetic": True}})
        assert knowledge.job(jid)["recursive"] is True
        assert knowledge.job(jid)["result_json"] == {"synthetic": True}
        with pytest.raises(RuntimeError, match="Worker leases"):
            knowledge.recover_local_jobs()
        assert knowledge.delete_document(doc_id, 2)
        assert knowledge.document_chunks(doc_id) == []
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(snapshots)
                .where(snapshots.c.document_id == uuid.UUID(doc_id))).scalar_one() == 0
    finally:
        engine.dispose()


def test_real_rag_chunk_backfill_is_restartable_and_versioned(postgres_database):
    from app.rag_backfill import backfill_current_chunks
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        document_id = uuid.uuid4()
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        snapshots = current_metadata.tables['knowledge_chunk_versions']
        with engine.begin() as db:
            db.execute(documents.insert().values(id=document_id, display_name='Synthetic backfill',
                stored_name='synthetic.txt', extension='.txt', size=10, sha256='f' * 64,
                status='ready', chunk_count=1, version=2, created_at=datetime.now(timezone.utc)))
            db.execute(chunks.insert().values(document_id=document_id, chunk_index=0,
                content='Old current evidence', tokenized='Old current evidence', block_type='paragraph'))
        assert backfill_current_chunks(engine, batch_size=1) == 1
        assert backfill_current_chunks(engine, batch_size=1) == 0
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == document_id).values(version=3))
            db.execute(chunks.update().where(chunks.c.document_id == document_id)
                .values(content='New current evidence', tokenized='New current evidence'))
        assert backfill_current_chunks(engine, batch_size=1) == 1
        with engine.connect() as db:
            rows = db.execute(sa.select(snapshots.c.document_version, snapshots.c.content)
                .where(snapshots.c.document_id == document_id)
                .order_by(snapshots.c.document_version)).all()
            assert rows == [(2, 'Old current evidence'), (3, 'New current evidence')]
        with engine.begin() as db:
            db.execute(documents.delete().where(documents.c.id == document_id))
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(snapshots)
                .where(snapshots.c.document_id == document_id)).scalar_one() == 0
    finally:
        engine.dispose()


def test_real_pgvector_scoped_current_version_search(postgres_database):
    import hashlib
    from app.persistence.identity_repository import IdentityRepository
    from app.rag_model_manifest import EMBEDDING_WEIGHT

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenants = [identity.create_tenant('Synthetic vector A'), identity.create_tenant('Synthetic vector B')]
        repositories = []
        profile_ids = []
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        snapshots = current_metadata.tables['knowledge_chunk_versions']
        profiles = current_metadata.tables['embedding_profiles']
        indexes = current_metadata.tables['knowledge_index_versions']
        now = datetime.now(timezone.utc)
        vector = [1.0] + [0.0] * 1023
        for number, tenant in enumerate(tenants):
            name = f'vector.admin.{number}'
            actor_id = identity.provision_user(name, 'synthetic-password-only', tenant, role='admin')
            token = identity.login(name, 'synthetic-password-only', tenant).token
            repositories.append(KnowledgeRepository(engine).authenticated(token))
            profile_id, index_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            profile_ids.append(profile_id)
            content = f'Synthetic tenant {number} evidence'
            digest = hashlib.sha256(content.encode()).hexdigest()
            with engine.begin() as db:
                db.execute(profiles.insert().values(tenant_id=uuid.UUID(tenant), profile_id=profile_id,
                    model_id=EMBEDDING_WEIGHT.repository, model_revision=EMBEDDING_WEIGHT.revision,
                    weights_sha256=EMBEDDING_WEIGHT.sha256, license_id=EMBEDDING_WEIGHT.license_id,
                    dimension=1024, distance='cosine', normalized=True, max_tokens=8192, created_at=now))
                db.execute(indexes.insert().values(tenant_id=uuid.UUID(tenant), index_id=index_id,
                    profile_id=profile_id, status='ready', manifest_sha256='a' * 64,
                    lexical_version='synthetic', chunker_version='synthetic', created_at=now))
                db.execute(documents.insert().values(id=document_id, tenant_id=uuid.UUID(tenant),
                    display_name=f'Synthetic {number}', stored_name=f'synthetic-{number}.txt',
                    extension='.txt', size=10, sha256='b' * 64, status='ready', chunk_count=1,
                    version=1, created_at=now, created_by=uuid.UUID(actor_id)))
                db.execute(chunks.insert().values(tenant_id=uuid.UUID(tenant), document_id=document_id,
                    chunk_index=0, content=content, tokenized=content, block_type='paragraph'))
                db.execute(snapshots.insert().values(tenant_id=uuid.UUID(tenant), document_id=document_id,
                    document_version=1, chunk_index=0, content_sha256=digest, content=content,
                    block_type='paragraph', metadata_json={}, parser_version='synthetic',
                    chunker_version='synthetic', created_at=now))
            repo = repositories[-1]
            with pytest.raises(ValueError, match='snapshot'):
                repo.store_embedding(profile_id=profile_id, document_id=document_id, document_version=1,
                    chunk_index=0, content_sha256='0' * 64, vector=vector)
            with engine.begin() as db:
                db.execute(snapshots.update().where(snapshots.c.document_id == document_id)
                    .values(content='Synthetic corrupt snapshot'))
            with pytest.raises(ValueError, match='content hash mismatch'):
                repo.store_embedding(profile_id=profile_id, document_id=document_id, document_version=1,
                    chunk_index=0, content_sha256=digest, vector=vector)
            with engine.begin() as db:
                db.execute(snapshots.update().where(snapshots.c.document_id == document_id)
                    .values(content=content))
            assert repo.store_embedding(profile_id=profile_id, document_id=document_id,
                document_version=1, chunk_index=0, content_sha256=digest, vector=vector)
            assert not repo.store_embedding(profile_id=profile_id, document_id=document_id,
                document_version=1, chunk_index=0, content_sha256=digest, vector=vector)
            assert repo.search_vector_candidates(vector, profile_id=profile_id, limit=3) == []
            with engine.begin() as db:
                db.execute(indexes.update().where(indexes.c.index_id == index_id)
                    .values(status='active', activated_at=now))
            rows = repo.search_vector_candidates(vector, profile_id=profile_id, limit=3)
            assert len(rows) == 1 and rows[0]['content'] == content
            assert rows[0]['distance'] == pytest.approx(0)
            with engine.begin() as db:
                db.execute(documents.update().where(documents.c.id == document_id).values(version=2))
            assert repo.search_vector_candidates(vector, profile_id=profile_id, limit=3) == []
        assert repositories[0].search_vector_candidates(vector, profile_id=profile_ids[1], limit=3) == []
    finally:
        engine.dispose()


def test_real_rag_index_worker_recovers_and_rechecks_document_version(postgres_database,monkeypatch):
    import hashlib
    from app.persistence.identity_repository import IdentityRepository
    from app.rag_index_admin import RagIndexAdmin
    from app.rag_indexer import RagIndexWorker

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenant = uuid.UUID(identity.create_tenant('Synthetic index tenant'))
        actor_id = uuid.UUID(identity.provision_user('synthetic.indexer',
            'synthetic-password-only', str(tenant), role='admin'))
        profile_id, index_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        snapshots = current_metadata.tables['knowledge_chunk_versions']
        embeddings = current_metadata.tables['knowledge_embeddings_1024']
        indexes = current_metadata.tables['knowledge_index_versions']
        profiles = current_metadata.tables['embedding_profiles']
        content = 'Synthetic current version content'
        now = datetime.now(timezone.utc)
        admin = RagIndexAdmin(engine)
        admin.stage(tenant_id=tenant, profile_id=profile_id, index_id=index_id,
            max_tokens=8192, lexical_version='synthetic', chunker_version='synthetic')
        with engine.begin() as db:
            db.execute(documents.insert().values(id=document_id, tenant_id=tenant,
                display_name='Synthetic worker', stored_name='synthetic-worker.txt', extension='.txt',
                size=10, sha256='a' * 64, status='ready', chunk_count=1, version=1,
                created_at=now, created_by=actor_id))
            db.execute(chunks.insert().values(tenant_id=tenant, document_id=document_id,
                chunk_index=0, content=content, tokenized=content, block_type='paragraph'))
            db.execute(snapshots.insert().values(tenant_id=tenant, document_id=document_id,
                document_version=1, chunk_index=0, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                content=content, block_type='paragraph', metadata_json={}, parser_version='synthetic',
                chunker_version='synthetic', created_at=now))

        class SyntheticGateway:
            def __init__(self, replace=False, corrupt=False):
                self.replace = replace
                self.corrupt = corrupt
                self.max_length = 8192
            def encode(self, texts, *, query=False):
                assert texts == [content] and not query
                if self.replace:
                    with engine.begin() as db:
                        db.execute(documents.update().where(documents.c.id == document_id).values(version=2))
                if self.corrupt:
                    with engine.begin() as db:
                        db.execute(snapshots.update().where(snapshots.c.document_id == document_id)
                            .values(content='Synthetic changed during inference'))
                return [[1.0] + [0.0] * 1023]

        mismatched = SyntheticGateway()
        mismatched.max_length = 1024
        with pytest.raises(ValueError, match='input limit'):
            RagIndexWorker(engine, tenant_id=tenant, profile_id=profile_id,
                           embedding_gateway=mismatched).run_batch()

        stale = RagIndexWorker(engine, tenant_id=tenant, profile_id=profile_id,
                               embedding_gateway=SyntheticGateway(replace=True))
        assert stale.run_batch() == 0
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 0
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == document_id).values(version=1))
        with pytest.raises(ValueError, match='Embeddings are incomplete'):
            admin.seal(tenant_id=tenant, index_id=index_id)
        with engine.begin() as db:
            db.execute(snapshots.update().where(snapshots.c.document_id == document_id)
                .values(content='Synthetic invalid hash before inference'))
        with pytest.raises(ValueError, match='snapshot content hash mismatch'):
            RagIndexWorker(engine, tenant_id=tenant, profile_id=profile_id,
                embedding_gateway=SyntheticGateway()).run_batch()
        with engine.begin() as db:
            db.execute(snapshots.update().where(snapshots.c.document_id == document_id).values(content=content))
        racing = RagIndexWorker(engine, tenant_id=tenant, profile_id=profile_id,
                                embedding_gateway=SyntheticGateway(corrupt=True))
        assert racing.run_batch() == 0
        with engine.begin() as db:
            db.execute(snapshots.update().where(snapshots.c.document_id == document_id).values(content=content))
        worker = RagIndexWorker(engine, tenant_id=tenant, profile_id=profile_id,
                                embedding_gateway=SyntheticGateway())
        assert worker.run_batch() == 1
        assert worker.run_batch() == 0
        sealed = admin.seal(tenant_id=tenant, index_id=index_id)
        assert sealed['current_chunks'] == 1 and len(sealed['manifest_sha256']) == 64
        with engine.begin() as db:
            db.execute(chunks.update().where(chunks.c.document_id == document_id)
                .values(content='Synthetic same but invalid hash'))
            db.execute(snapshots.update().where(snapshots.c.document_id == document_id)
                .values(content='Synthetic same but invalid hash'))
        with pytest.raises(ValueError, match='snapshot content hash mismatch'):
            admin.activate(tenant_id=tenant, index_id=index_id, expected_active_index_id=None)
        with engine.begin() as db:
            db.execute(chunks.update().where(chunks.c.document_id == document_id).values(content=content))
            db.execute(snapshots.update().where(snapshots.c.document_id == document_id).values(content=content))
        with engine.begin() as db:
            db.execute(chunks.update().where(chunks.c.document_id == document_id)
                .values(content='Synthetic inconsistent current chunk'))
        with pytest.raises(ValueError, match='snapshots are incomplete or inconsistent'):
            admin.activate(tenant_id=tenant, index_id=index_id, expected_active_index_id=None)
        with engine.begin() as db:
            db.execute(chunks.update().where(chunks.c.document_id == document_id).values(content=content))
        with engine.begin() as db:
            db.execute(profiles.update().where(profiles.c.profile_id == profile_id).values(max_tokens=4096))
        with pytest.raises(ValueError, match='changed after sealing'):
            admin.activate(tenant_id=tenant, index_id=index_id, expected_active_index_id=None)
        with engine.begin() as db:
            db.execute(profiles.update().where(profiles.c.profile_id == profile_id).values(max_tokens=8192))
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == document_id).values(version=2))
        with pytest.raises(ValueError, match='snapshots are incomplete'):
            admin.activate(tenant_id=tenant, index_id=index_id, expected_active_index_id=None)
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == document_id).values(version=1))
        with pytest.raises(ValueError, match='changed since rollout approval'):
            admin.activate(tenant_id=tenant, index_id=index_id,
                expected_active_index_id=uuid.uuid4())
        assert admin.activate(tenant_id=tenant, index_id=index_id,
            expected_active_index_id=None) == sealed
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 1
            assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == index_id)).scalar_one() == 'active'
        next_profile, next_index = uuid.uuid4(), uuid.uuid4()
        admin.stage(tenant_id=tenant, profile_id=next_profile, index_id=next_index,
            max_tokens=8192, lexical_version='synthetic-v2', chunker_version='synthetic')
        assert RagIndexWorker(engine, tenant_id=tenant, profile_id=next_profile,
                              embedding_gateway=SyntheticGateway()).run_batch() == 1
        admin.seal(tenant_id=tenant, index_id=next_index)
        query = [1.0] + [0.0] * 1023
        shadow_locks = []
        def record_shadow_lock(connection, cursor, statement, parameters, context, executemany):
            if 'pg_advisory_xact_lock' in statement:
                shadow_locks.append(statement)
        sa.event.listen(engine, 'before_cursor_execute', record_shadow_lock)
        try:
            shadow = admin.shadow_compare(tenant_id=tenant, index_id=next_index,
                vectors=[query], top_k=1, max_tokens=8192)
        finally:
            sa.event.remove(engine, 'before_cursor_execute', record_shadow_lock)
        assert len(shadow_locks) == 1 and 'pg_advisory_xact_lock_shared' in shadow_locks[0]
        assert shadow['count'] == 1 and shadow['mean_overlap_at_k'] == 1
        assert shadow['top1_changed'] == shadow['active_empty'] == shadow['shadow_empty'] == 0
        with pytest.raises(ValueError, match='ready index'):
            admin.shadow_compare(tenant_id=tenant, index_id=index_id,
                vectors=[query], top_k=1, max_tokens=8192)
        with pytest.raises(ValueError, match='normalized'):
            admin.shadow_compare(tenant_id=tenant, index_id=next_index,
                vectors=[[2.0] + [0.0] * 1023], top_k=1, max_tokens=8192)
        with pytest.raises(ValueError, match='changed since rollout approval'):
            admin.activate(tenant_id=tenant, index_id=next_index,
                expected_active_index_id=None)
        admin.activate(tenant_id=tenant, index_id=next_index,
            expected_active_index_id=index_id)
        with engine.connect() as db:
            assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == index_id)).scalar_one() == 'retired'
            assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == next_index)).scalar_one() == 'active'
        reader = KnowledgeRepository(engine).authenticated(identity.login('synthetic.indexer',
            'synthetic-password-only', str(tenant)).token)
        assert reader.search_vector_candidates(query, profile_id=profile_id, limit=3) == []
        assert len(reader.search_vector_candidates(query, profile_id=next_profile, limit=3)) == 1
        rollback_index = uuid.uuid4()
        admin.stage_existing(tenant_id=tenant, profile_id=profile_id, index_id=rollback_index,
            lexical_version='synthetic-rollback', chunker_version='synthetic')
        admin.seal(tenant_id=tenant, index_id=rollback_index)
        admin.activate(tenant_id=tenant, index_id=rollback_index,
            expected_active_index_id=next_index)
        assert len(reader.search_vector_candidates(query, profile_id=profile_id, limit=3)) == 1
        assert reader.search_vector_candidates(query, profile_id=next_profile, limit=3) == []
        # The old profile is retained for rollback while its document version
        # remains current. A superseded version may then be pruned in batches.
        assert admin.prune_stale_embeddings(tenant_id=tenant, batch_size=1) == 0
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == document_id).values(version=2))
        other_tenant = uuid.UUID(identity.create_tenant('Synthetic prune isolation tenant'))
        other_actor = uuid.UUID(identity.provision_user('synthetic.prune.other',
            'synthetic-password-only', str(other_tenant), role='admin'))
        other_profile, other_index, other_document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        admin.stage(tenant_id=other_tenant, profile_id=other_profile, index_id=other_index,
            max_tokens=8192, lexical_version='synthetic', chunker_version='synthetic')
        with engine.begin() as db:
            db.execute(documents.insert().values(id=other_document, tenant_id=other_tenant,
                display_name='Other tenant synthetic', stored_name='other-tenant.txt', extension='.txt',
                size=10, sha256='b' * 64, status='ready', chunk_count=1, version=1,
                created_at=now, created_by=other_actor))
            db.execute(chunks.insert().values(tenant_id=other_tenant, document_id=other_document,
                chunk_index=0, content=content, tokenized=content, block_type='paragraph'))
            db.execute(snapshots.insert().values(tenant_id=other_tenant, document_id=other_document,
                document_version=1, chunk_index=0, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                content=content, block_type='paragraph', metadata_json={}, parser_version='synthetic',
                chunker_version='synthetic', created_at=now))
        assert RagIndexWorker(engine, tenant_id=other_tenant, profile_id=other_profile,
                              embedding_gateway=SyntheticGateway()).run_batch() == 1
        with engine.begin() as db:
            db.execute(documents.update().where(documents.c.id == other_document).values(version=2))
        import subprocess
        import sys
        prune_command = [sys.executable, 'scripts/prune_rag_embeddings.py',
            '--database-url', postgres_database, '--tenant-id', str(tenant), '--batch-size', '1']
        denied_prune = subprocess.run(prune_command, capture_output=True, text=True)
        assert denied_prune.returncode != 0
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 3
        def audit_unavailable(*args,**kwargs):
            raise RuntimeError('Synthetic RAG prune audit unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(admin,'_audit',audit_unavailable)
            with pytest.raises(RuntimeError,match='prune audit unavailable'):
                admin.prune_stale_embeddings(tenant_id=tenant,batch_size=1)
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 3
        accepted_prune = subprocess.run(prune_command + ['--confirm-stale-prune'],
            capture_output=True, text=True)
        assert accepted_prune.returncode == 0, accepted_prune.stderr
        assert accepted_prune.stdout.strip() == 'Stale RAG embeddings removed: 1'
        assert admin.prune_stale_embeddings(tenant_id=tenant, batch_size=1) == 1
        assert admin.prune_stale_embeddings(tenant_id=tenant, batch_size=1) == 0
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 1
            assert db.execute(sa.select(sa.func.count()).select_from(snapshots)).scalar_one() == 2
        assert admin.prune_stale_embeddings(tenant_id=other_tenant, batch_size=1) == 1
        with engine.connect() as db:
            assert db.execute(sa.select(sa.func.count()).select_from(embeddings)).scalar_one() == 0
            audit=current_metadata.tables['audit_logs']
            prune_events=db.execute(sa.select(audit.c.metadata_json).where(
                audit.c.action=='rag.index.prune_stale')).scalars().all()
            assert len(prune_events)==3 and all(row['deleted_count']==1 for row in prune_events)
        with pytest.raises(ValueError, match='batch size'):
            admin.prune_stale_embeddings(tenant_id=tenant, batch_size=1001)
    finally:
        engine.dispose()


@pytest.mark.requires_local_rag_model
def test_real_local_model_pgvector_index_cli(postgres_database):
    import hashlib
    import subprocess
    import sys
    from pathlib import Path
    from app.persistence.identity_repository import IdentityRepository
    from app.rag_index_admin import RagIndexAdmin
    from app.rag_local import LocalEmbeddingGateway, LocalReranker
    from app.knowledge import KnowledgeService
    from app.knowledge import TOKENIZER_VERSION
    from app.knowledge_models import CHUNKER_VERSION
    from app.rag_model_manifest import RERANKER_WEIGHT

    root = os.environ.get('TEST_RAG_MODEL_ROOT')
    if not root:
        pytest.skip('Explicit verified local RAG model root is required')
    model_dir = Path(root) / 'embedding'
    reranker_dir = Path(root) / 'reranker'
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant('Synthetic live vector tenant')
        actor = identity.provision_user('synthetic.live.index', 'synthetic-password-only',
                                        tenant, role='admin')
        profile_id, index_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        admin = RagIndexAdmin(engine)
        admin.stage(tenant_id=tenant, profile_id=profile_id, index_id=index_id,
            max_tokens=256, lexical_version=TOKENIZER_VERSION,
            chunker_version=CHUNKER_VERSION, reranker_version=RERANKER_WEIGHT.revision)
        content = '退货授权需要订单号和产品批次。'
        now = datetime.now(timezone.utc)
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        snapshots = current_metadata.tables['knowledge_chunk_versions']
        with engine.begin() as db:
            db.execute(documents.insert().values(id=document_id, tenant_id=uuid.UUID(tenant),
                created_by=uuid.UUID(actor), display_name='Synthetic live document',
                stored_name='synthetic-live.txt', extension='.txt', size=20,
                sha256='c' * 64, status='ready', chunk_count=1, version=1, created_at=now))
            db.execute(chunks.insert().values(tenant_id=uuid.UUID(tenant), document_id=document_id,
                chunk_index=0, content=content, tokenized=content, block_type='paragraph'))
            db.execute(snapshots.insert().values(tenant_id=uuid.UUID(tenant),
                document_id=document_id, document_version=1, chunk_index=0,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(), content=content,
                block_type='paragraph', metadata_json={}, parser_version='synthetic',
                chunker_version='synthetic', created_at=now))
        process = subprocess.run([sys.executable, 'scripts/rag_index_worker.py',
            '--database-url', postgres_database, '--tenant-id', tenant,
            '--profile-id', str(profile_id), '--model-dir', str(model_dir),
            '--device', 'cuda', '--max-length', '256', '--max-batches', '2'],
            capture_output=True, text=True, timeout=120)
        assert process.returncode == 0, process.stderr
        assert 'inserted: 1' in process.stdout
        admin.seal(tenant_id=tenant, index_id=index_id)
        admin.activate(tenant_id=tenant, index_id=index_id, expected_active_index_id=None)
        token = identity.login('synthetic.live.index', 'synthetic-password-only', tenant).token
        embedding = LocalEmbeddingGateway(model_dir, device='cuda', max_length=256)
        query = embedding.encode(
            ['退货要准备哪些资料？'], query=True)[0]
        scoped = KnowledgeRepository(engine).authenticated(token)
        rows = scoped.search_vector_candidates(
            query, profile_id=profile_id, limit=3)
        assert len(rows) == 1 and rows[0]['content'] == content
        service = KnowledgeService(repository=scoped, embedding_gateway=embedding,
            reranker=LocalReranker(reranker_dir, device='cuda', max_length=256))
        hybrid = service.search('退货要准备哪些资料？')
        assert len(hybrid) == 1 and hybrid[0]['content'] == content
        assert hybrid[0]['ranking']['fusion_boost'] > 0
        assert 'reranker_score' in hybrid[0]['ranking']
    finally:
        engine.dispose()


def test_real_runtime_role_rag_acl_gate_rejects_owner(postgres_database):
    from app.persistence.runtime_role import require_application_role
    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        with engine.begin() as db:
            with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                require_application_role(db, 'api')
            with pytest.raises(RuntimeError, match='Unsafe PostgreSQL application role'):
                require_application_role(db, 'worker')
    finally:
        engine.dispose()


def test_real_answer_commit_locks_and_rechecks_cited_chunk(postgres_database):
    import hashlib
    from app.persistence.identity_repository import IdentityRepository
    from app.knowledge_retrieval import KnowledgeEvidenceChanged

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=1)
    try:
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant('Synthetic cited tenant')
        actor = identity.provision_user('synthetic.citation.admin', 'synthetic-password-only',
                                        tenant, role='admin')
        token = identity.login('synthetic.citation.admin', 'synthetic-password-only', tenant).token
        document_id, session_id, run_id = (str(uuid.uuid4()) for _ in range(3))
        content = 'Synthetic approved evidence'
        now = datetime.now(timezone.utc).isoformat()
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        with engine.begin() as db:
            db.execute(documents.insert().values(id=uuid.UUID(document_id),
                tenant_id=uuid.UUID(tenant), created_by=uuid.UUID(actor),
                display_name='Synthetic evidence', stored_name='synthetic-evidence.txt',
                extension='.txt', size=10, sha256='d' * 64, status='ready',
                chunk_count=1, version=1, created_at=datetime.now(timezone.utc)))
            chunk_id = db.execute(chunks.insert().values(tenant_id=uuid.UUID(tenant),
                document_id=uuid.UUID(document_id), chunk_index=0, content=content,
                tokenized=content, block_type='paragraph').returning(chunks.c.id)).scalar_one()
        chat = ChatRepository(engine).authenticated(token)
        assert chat.start_run(session_id=session_id, run_id=run_id, user_id=str(uuid.uuid4()),
            title='Synthetic citation', content='Synthetic question', provider='deepseek',
            model='synthetic', prompt_version_id=None, now=now)
        arguments = dict(run_id=run_id, session_id=session_id, message_id=str(uuid.uuid4()),
            content='Synthetic answer', provider='deepseek', model='synthetic', citations=[], now=now)
        with pytest.raises(KnowledgeEvidenceChanged):
            chat.complete_run(**arguments, evidence_sources=[dict(id=chunk_id,
                document_version=1, content_sha256='0' * 64)])
        with engine.connect() as db:
            runs = current_metadata.tables['agent_runs']
            messages = current_metadata.tables['messages']
            assert db.execute(sa.select(runs.c.status).where(runs.c.id == uuid.UUID(run_id))).scalar_one() == 'running'
            assert db.execute(sa.select(sa.func.count()).select_from(messages).where(
                messages.c.session_id == uuid.UUID(session_id), messages.c.role == 'assistant')).scalar_one() == 0
        assert chat.complete_run(**arguments, evidence_sources=[dict(id=chunk_id,
            document_version=1, content_sha256=hashlib.sha256(content.encode()).hexdigest())])
    finally:
        engine.dispose()


def test_real_answer_commit_atomically_checks_pinned_rag_index(postgres_database):
    from threading import Event
    from app.persistence.identity_repository import IdentityRepository
    from app.knowledge_retrieval import KnowledgeIndexChanged
    from app.rag_index_admin import RAG_PUBLICATION_LOCK
    from app.rag_model_manifest import EMBEDDING_WEIGHT

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=2)
    try:
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant('Synthetic pinned-answer tenant')
        identity.provision_user('synthetic.pinned.answer', 'synthetic-password-only', tenant,
                                role='admin')
        token = identity.login('synthetic.pinned.answer', 'synthetic-password-only', tenant).token
        profile_id, old_index, new_index = (uuid.uuid4() for _ in range(3))
        now = datetime.now(timezone.utc)
        profiles = current_metadata.tables['embedding_profiles']
        indexes = current_metadata.tables['knowledge_index_versions']
        with engine.begin() as db:
            db.execute(profiles.insert().values(tenant_id=uuid.UUID(tenant), profile_id=profile_id,
                model_id=EMBEDDING_WEIGHT.repository, model_revision=EMBEDDING_WEIGHT.revision,
                weights_sha256=EMBEDDING_WEIGHT.sha256, license_id=EMBEDDING_WEIGHT.license_id,
                dimension=1024, distance='cosine', normalized=True, max_tokens=256,
                created_at=now))
            for index_id, status in ((old_index, 'active'), (new_index, 'ready')):
                db.execute(indexes.insert().values(tenant_id=uuid.UUID(tenant), index_id=index_id,
                    profile_id=profile_id, status=status, manifest_sha256='a' * 64,
                    lexical_version='synthetic', chunker_version='synthetic',
                    created_at=now, activated_at=now if status == 'active' else None))
        chat = ChatRepository(engine).authenticated(token)
        def start():
            session_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
            assert chat.start_run(session_id=session_id, run_id=run_id,
                user_id=str(uuid.uuid4()), title='Synthetic index pin', content='Synthetic question',
                provider='deepseek', model='synthetic', prompt_version_id=None, now=now.isoformat())
            return dict(session_id=session_id, run_id=run_id, message_id=str(uuid.uuid4()),
                content='Synthetic answer', provider='deepseek', model='synthetic',
                citations=[], now=now.isoformat(), index_pinned=True)
        first = start()
        before_insert, allow_insert = Event(), Event()
        def hold_assistant_insert(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO messages') and not before_insert.is_set():
                before_insert.set()
                assert allow_insert.wait(5), 'Assistant insert remained held too long'
        def switch_index():
            with engine.begin() as db:
                db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),
                           {'key': RAG_PUBLICATION_LOCK})
                db.execute(indexes.update().where(indexes.c.index_id == old_index).values(status='retired'))
                db.execute(indexes.update().where(indexes.c.index_id == new_index).values(
                    status='active', activated_at=now))
        sa.event.listen(engine, 'before_cursor_execute', hold_assistant_insert)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                committing = pool.submit(chat.complete_run, **first, index_id=old_index)
                assert before_insert.wait(5), 'Assistant commit did not reach insert'
                switching = pool.submit(switch_index)
                # The publication lock must keep the switch behind answer commit.
                with pytest.raises(FutureTimeoutError):
                    switching.result(timeout=0.2)
                allow_insert.set()
                assert committing.result(timeout=5)
                switching.result(timeout=5)
        finally:
            allow_insert.set()
            sa.event.remove(engine, 'before_cursor_execute', hold_assistant_insert)
        second = start()
        with pytest.raises(KnowledgeIndexChanged):
            chat.complete_run(**second, index_id=old_index)
        with engine.connect() as db:
            runs = current_metadata.tables['agent_runs']
            messages = current_metadata.tables['messages']
            assert db.execute(sa.select(runs.c.status).where(runs.c.id == uuid.UUID(second['run_id']))).scalar_one() == 'running'
            assert db.execute(sa.select(sa.func.count()).select_from(messages).where(
                messages.c.session_id == uuid.UUID(second['session_id']),
                messages.c.role == 'assistant')).scalar_one() == 0
        assert chat.complete_run(**second, index_id=new_index)
    finally:
        engine.dispose()


def test_rag_shadow_shared_publication_lock_allows_answer_but_blocks_activation(postgres_database):
    from app.rag_index_admin import RagIndexAdmin

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=3)
    admin = RagIndexAdmin(engine)
    def guarded(shared):
        with engine.begin() as db:
            admin._guard(db, shared=shared)
        return True
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with engine.begin() as shadow_db:
                admin._guard(shadow_db, shared=True)
                assert pool.submit(guarded, True).result(timeout=3)
                activation = pool.submit(guarded, False)
                with pytest.raises(FutureTimeoutError):
                    activation.result(timeout=0.2)
            assert activation.result(timeout=3)
    finally:
        engine.dispose()


def test_real_search_refills_after_top_document_changes(postgres_database, tmp_path, monkeypatch):
    from app.persistence.identity_repository import IdentityRepository
    from app.knowledge import KnowledgeService

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=2)
    try:
        identity = IdentityRepository(engine)
        tenant = identity.create_tenant('Synthetic reserve tenant')
        identity.provision_user('synthetic.reserve', 'synthetic-password-only', tenant, role='admin')
        token = identity.login('synthetic.reserve', 'synthetic-password-only', tenant).token
        repository = KnowledgeRepository(engine).authenticated(token)
        service = KnowledgeService(repository=repository, knowledge_dir=tmp_path/'knowledge',
            asset_dir=tmp_path/'assets', legacy_upload_dir=tmp_path/'legacy')
        for name in ('alpha', 'beta'):
            service.ingest(name+'.txt', f'退货需要订单号。Synthetic {name}.'.encode())
        first = service.search('退货需要订单号', limit=1)
        assert len(first) == 1
        document_id = first[0]['document_id']
        original_assets = repository.assets
        def replace_before_disclosure(document_ids):
            assets = original_assets(document_ids)
            documents = current_metadata.tables['documents']
            with engine.begin() as db:
                db.execute(documents.update().where(documents.c.id == uuid.UUID(document_id))
                    .values(version=documents.c.version + 1))
            return assets
        monkeypatch.setattr(repository, 'assets', replace_before_disclosure)
        results = service.search('退货需要订单号', limit=1)
        assert len(results) == 1 and results[0]['document_id'] != document_id
    finally:
        engine.dispose()


def test_real_blind_search_binds_tenant_role_and_revocation(postgres_database):
    from app.identity import AuthenticationError, AuthorizationError
    from app.persistence.identity_repository import IdentityRepository
    from app.rag_blind_adapter import AuthenticatedBlindSearch, BlindBinding

    command.upgrade(Config('alembic.ini'), 'head')
    engine = build_engine(postgres_database, pool_size=2)
    try:
        identity = IdentityRepository(engine)
        documents = current_metadata.tables['documents']
        chunks = current_metadata.tables['knowledge_chunks']
        bindings = {}
        tokens = []
        for number in range(2):
            alias = f'synthetic-tenant-{number}'
            tenant = identity.create_tenant(alias)
            actor = identity.provision_user(f'synthetic.blind.{number}',
                'synthetic-password-only', tenant, role='customer')
            token = identity.login(f'synthetic.blind.{number}',
                'synthetic-password-only', tenant).token
            tokens.append(token)
            document_id = str(uuid.uuid4())
            with engine.begin() as db:
                db.execute(documents.insert().values(id=uuid.UUID(document_id),
                    tenant_id=uuid.UUID(tenant), created_by=uuid.UUID(actor),
                    display_name='Synthetic blind source', stored_name=f'synthetic-{number}.txt',
                    extension='.txt', size=10, sha256=f'{number + 1}' * 64,
                    status='ready', chunk_count=1, version=1,
                    created_at=datetime.now(timezone.utc)))
                db.execute(chunks.insert().values(tenant_id=uuid.UUID(tenant),
                    document_id=uuid.UUID(document_id), chunk_index=0,
                    content=f'warranty synthetic tenant {number}',
                    tokenized='warranty synthetic tenant', block_type='paragraph'))
            bindings[(alias, 'customer')] = BlindBinding(tenant_id=tenant, role='customer',
                token=token, document_ref_by_id={document_id: f'synthetic-ref-{number}'})
        adapter = AuthenticatedBlindSearch(engine, bindings)
        assert adapter('synthetic-tenant-0', 'customer', 'warranty', limit=3)[0][
            'document_ref'] == 'synthetic-ref-0'
        assert adapter('synthetic-tenant-1', 'customer', 'warranty', limit=3)[0][
            'document_ref'] == 'synthetic-ref-1'
        with pytest.raises(AuthorizationError, match='binding unavailable'):
            adapter('synthetic-tenant-0', 'admin', 'warranty', limit=3)
        swapped = dict(bindings)
        swapped[('synthetic-tenant-0', 'customer')] = BlindBinding(
            tenant_id=bindings[('synthetic-tenant-0', 'customer')].tenant_id,
            role='customer', token=tokens[1],
            document_ref_by_id=bindings[('synthetic-tenant-0', 'customer')].document_ref_by_id)
        with pytest.raises(AuthorizationError, match='binding changed'):
            AuthenticatedBlindSearch(engine, swapped)(
                'synthetic-tenant-0', 'customer', 'warranty', limit=3)
        identity.logout(tokens[0])
        with pytest.raises(AuthenticationError):
            adapter('synthetic-tenant-0', 'customer', 'warranty', limit=3)
    finally:
        engine.dispose()
