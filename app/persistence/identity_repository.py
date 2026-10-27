"""PostgreSQL-backed identities; provisioning methods are trusted internal APIs.

Not exposed as self-registration endpoints. Password verification runs outside
transactions; a subsequent locked version check prevents password-reset races.
"""

import secrets
import uuid
from datetime import timedelta

import sqlalchemy as sa
from app.identity import AuthenticationError, LoginSession, Principal, normalize_username, password_hash, token_hash, verify_password
from app.persistence.base import Repository
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID


class IdentityRepository(Repository):
    table_names = ("identity_tenants", "identity_users", "identity_memberships", "identity_sessions", "identity_events")

    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Production identity requires PostgreSQL")
        super().__init__(engine)

    def create_tenant(self, name, *, active=True):
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("Invalid tenant name")
        identifier = uuid.uuid4()
        with self.engine.begin() as db:
            db.execute(self.tables["identity_tenants"].insert().values(id=identifier, name=name.strip(), active=active))
        return str(identifier)

    def provision_user(self, username, password, tenant_id, *, role="customer"):
        if role not in {"customer", "support", "admin"}:
            raise ValueError("Invalid role")
        username, encoded, uid, tid = normalize_username(username), password_hash(password), uuid.uuid4(), uuid.UUID(tenant_id)
        if str(tid) == LEGACY_TENANT_ID:
            raise ValueError("Legacy quarantine cannot receive login accounts")
        u, m = self.tables["identity_users"], self.tables["identity_memberships"]
        with self.engine.begin() as db:
            db.execute(u.insert().values(id=uid, username=username, password_hash=encoded, created_at=sa.func.now()))
            db.execute(m.insert().values(tenant_id=tid, user_id=uid, role=role))
            self._event(db, uid, tid, "identity.provision")
        return str(uid)

    def _event(self, db, uid, tid, action):
        db.execute(self.tables["identity_events"].insert().values(id=uuid.uuid4(), user_id=uid, tenant_id=tid,
            action=action, created_at=sa.func.now()))

    def login(self, username, password, tenant_id, *, lifetime=timedelta(hours=8), admission_permit=None):
        if not timedelta(minutes=1) <= lifetime <= timedelta(hours=8):
            raise ValueError("Invalid session lifetime")
        try:
            username, tid = normalize_username(username), uuid.UUID(tenant_id)
            if str(tid) == LEGACY_TENANT_ID:
                raise AuthenticationError()
        except (ValueError, TypeError, AttributeError):
            raise AuthenticationError() from None
        u, m, t, s = (
            self.tables[name] for name in ("identity_users", "identity_memberships", "identity_tenants", "identity_sessions"))
        with self.engine.connect() as db:
            snapshot = db.execute(sa.select(u).where(u.c.username == username)).mappings().first()
        # Unknown accounts pay the same KDF cost, rather than a cheap lookup-only
        # response. Public endpoints still require durable/edge admission limits.
        verifier = snapshot["password_hash"] if snapshot else "scrypt-v1$" + "00" * 16 + "$" + "00" * 32
        valid = verify_password(password, verifier)
        if not snapshot or not valid:
            raise AuthenticationError()
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as db:
            if admission_permit is not None:
                from app.login import LoginAdmission
                LoginAdmission.require_current(db, admission_permit)
            user = db.execute(sa.select(u).where(u.c.id == snapshot["id"]).with_for_update()).mappings().one()
            membership = db.execute(sa.select(m.c.role).join(t, t.c.id == m.c.tenant_id).where(
                m.c.user_id == user["id"], m.c.tenant_id == tid, m.c.active.is_(True), t.c.active.is_(True))).first()
            if not user["active"] or user["password_hash"] != snapshot["password_hash"] or user["auth_version"] != snapshot["auth_version"] or not membership:
                raise AuthenticationError()
            now = db.execute(sa.select(sa.func.now())).scalar_one()
            db.execute(s.insert().values(token_hash=token_hash(token), user_id=user["id"], tenant_id=tid,
                auth_version=user["auth_version"], created_at=now, expires_at=now + lifetime))
            self._event(db, user["id"], tid, "identity.login")
        return LoginSession(Principal(str(user["id"]), str(tid), membership.role), now + lifetime, token)

    def resolve(self, token, *, connection=None):
        hashed = token_hash(token)
        s, u, m, t = (self.tables[name] for name in ("identity_sessions", "identity_users", "identity_memberships", "identity_tenants"))
        query = sa.select(u.c.id, s.c.tenant_id, m.c.role).select_from(s.join(u, u.c.id == s.c.user_id)
            .join(m, sa.and_(m.c.user_id == s.c.user_id, m.c.tenant_id == s.c.tenant_id)).join(t, t.c.id == s.c.tenant_id)).where(
                s.c.token_hash == hashed, s.c.revoked_at.is_(None), s.c.expires_at > sa.func.now(),
                s.c.auth_version == u.c.auth_version, s.c.tenant_id != uuid.UUID(LEGACY_TENANT_ID),
                u.c.active.is_(True), m.c.active.is_(True), t.c.active.is_(True))
        if connection is None:
            with self.engine.connect() as db:
                row = db.execute(query).first()
        else:
            row = connection.execute(query).first()
        if not row:
            raise AuthenticationError()
        return Principal(str(row.id), str(row.tenant_id), row.role)

    def logout(self, token):
        hashed, s = token_hash(token), self.tables["identity_sessions"]
        with self.engine.begin() as db:
            row = db.execute(s.update().where(s.c.token_hash == hashed, s.c.revoked_at.is_(None))
                .values(revoked_at=sa.func.now()).returning(s.c.user_id, s.c.tenant_id)).first()
            if row:
                self._event(db, row.user_id, row.tenant_id, "identity.logout")
        return bool(row)

    def reset_password(self, user_id, password):
        encoded, uid = password_hash(password), uuid.UUID(user_id)
        u = self.tables["identity_users"]
        with self.engine.begin() as db:
            changed = db.execute(u.update().where(u.c.id == uid).values(password_hash=encoded, auth_version=u.c.auth_version + 1)).rowcount
            if not changed:
                raise ValueError("Unknown user")
            self._event(db, uid, None, "identity.password_reset")

    def update_membership(self, tenant_id, user_id, *, role, active):
        if role not in {"customer", "support", "admin"} or not isinstance(active, bool):
            raise ValueError("Invalid membership")
        m, tid, uid = self.tables["identity_memberships"], uuid.UUID(tenant_id), uuid.UUID(user_id)
        with self.engine.begin() as db:
            changed = db.execute(m.update().where(m.c.tenant_id == tid, m.c.user_id == uid).values(role=role, active=active)).rowcount
            if not changed:
                raise ValueError("Unknown membership")
            self._revoke(db, user_id=uid, tenant_id=tid)
            self._event(db, uid, tid, "identity.membership_update")

    def _revoke(self, db, *, user_id=None, tenant_id=None):
        sessions = self.tables["identity_sessions"]
        query = sessions.update().where(sessions.c.revoked_at.is_(None))
        if user_id is not None:
            query = query.where(sessions.c.user_id == user_id)
        if tenant_id is not None:
            query = query.where(sessions.c.tenant_id == tenant_id)
        if user_id is None and tenant_id is None:
            raise ValueError("Revocation requires a concrete identity scope")
        db.execute(query.values(revoked_at=sa.func.now()))

    def set_user_active(self, user_id, active):
        if not isinstance(active, bool):
            raise ValueError("Invalid user state")
        uid, users = uuid.UUID(user_id), self.tables["identity_users"]
        with self.engine.begin() as db:
            if not db.execute(users.update().where(users.c.id == uid)
                .values(active=active, auth_version=users.c.auth_version + 1)).rowcount:
                raise ValueError("Unknown user")
            self._revoke(db, user_id=uid)
            self._event(db, uid, None, "identity.user_state")

    def set_tenant_active(self, tenant_id, active):
        if not isinstance(active, bool):
            raise ValueError("Invalid tenant state")
        tid, tenants = uuid.UUID(tenant_id), self.tables["identity_tenants"]
        if str(tid) == LEGACY_TENANT_ID:
            raise ValueError("Legacy quarantine cannot be activated")
        with self.engine.begin() as db:
            if not db.execute(tenants.update().where(tenants.c.id == tid).values(active=active)).rowcount:
                raise ValueError("Unknown tenant")
            self._revoke(db, tenant_id=tid)
