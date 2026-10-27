"""Tenant-bound management persistence; HTTP assembly is a separate boundary."""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.persistence.base import LocalRepository, wire
from app.persistence.chat_repository import ChatRepository
from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
from app.database import utc_now
from app.identity import PERMISSIONS


class AdminRepository(ChatRepository):
    table_names = ChatRepository.table_names + ("prompt_versions", "session_notes", "audit_logs", "provider_configs", "app_settings", "documents", "knowledge_chunks")

    def _guard(self, db, permission="chat.tenant"):
        principal = self._principal(db)
        if principal:
            principal.require(permission)
        return principal

    def _record_scope(self, db, table):
        if "tenant_id" not in table.c:
            return sa.true()
        principal = self._principal(db)
        tenant = principal.tenant_id if principal else LEGACY_TENANT_ID
        return table.c.tenant_id == self.values(table, tenant_id=tenant)["tenant_id"]

    def _write_audit(self, db, *, action, entity_type, entity_id, summary, metadata, now):
        t = self.tables["audit_logs"]
        principal = self._principal(db)
        identity = {}
        if "tenant_id" in t.c:
            identity = {"tenant_id": principal.tenant_id if principal else LEGACY_TENANT_ID,
                        "actor_id": principal.user_id if principal else None,
                        "actor": principal.user_id if principal else "local_admin"}
        db.execute(t.insert().values(**self.values(t, id=str(uuid.uuid4()), action=action, entity_type=entity_type,
            entity_id=entity_id, summary=summary, metadata_json=metadata, created_at=now, **identity)))

    def audit(self, *, action, entity_type, entity_id, summary, metadata, now):
        with self.engine.begin() as db:
            self._guard(db)
            self._write_audit(db, action=action, entity_type=entity_type, entity_id=entity_id, summary=summary, metadata=metadata, now=now)

    def managed_sessions(self, filters, page, page_size):
        s, r, m, c = (self.tables[name] for name in ("chat_sessions", "agent_runs", "messages", "contacts"))
        latest = r.alias("latest")
        latest_id = sa.select(latest.c.id).where(latest.c.session_id == s.c.id).order_by(latest.c.started_at.desc(), latest.c.id.desc()).limit(1).correlate(s).scalar_subquery()
        source = s.outerjoin(r, r.c.id == latest_id)
        clauses = [self.lifecycle(s, filters.get("lifecycle", "all"))]
        for name, column in (("management_status", s.c.management_status), ("assignee", s.c.assignee), ("provider", r.c.provider), ("run_status", r.c.status)):
            if filters.get(name):
                clauses.append(column == filters[name])
        if filters.get("q"):
            term = filters["q"]
            clauses.append(sa.or_(s.c.title.contains(term, autoescape=True), sa.exists(sa.select(m.c.id).where(m.c.session_id == s.c.id, m.c.content.contains(term, autoescape=True)))))
        if filters.get("tag"):
            if self.engine.dialect.name == "postgresql":
                clauses.append(sa.cast(s.c.tags_json, JSONB).contains([filters["tag"]]))
            else:
                tags = sa.func.json_each(s.c.tags_json).table_valued("value")
                clauses.append(sa.exists(sa.select(1).select_from(tags).where(tags.c.value == filters["tag"])))
        for key, operator in (("date_from", "ge"), ("date_to", "le")):
            if filters.get(key):
                value = self.values(s, updated_at=filters[key])["updated_at"]
                clauses.append(s.c.updated_at >= value if operator == "ge" else s.c.updated_at <= value)
        if filters.get("has_contact") is not None:
            exists = sa.exists(sa.select(c.c.id).where(c.c.session_id == s.c.id))
            clauses.append(exists if filters["has_contact"] else ~exists)
        def count(table, *extra):
            return sa.select(sa.func.count()).where(table.c.session_id == s.c.id, *extra).correlate(s).scalar_subquery()
        query = sa.select(s, r.c.provider, r.c.model, r.c.status.label("last_run_status"), r.c.error_code.label("last_error_code"),
                          count(m, m.c.status == "complete").label("message_count"), count(r).label("run_count"),
                          count(r, r.c.status == "error").label("error_count"), count(c).label("contact_count")).select_from(source).where(*clauses)
        with self.engine.connect() as db:
            self._guard(db)
            clauses.append(self._scope(db, s))
            total = db.execute(sa.select(sa.func.count()).select_from(source).where(*clauses)).scalar_one()
            items = [wire(row) for row in db.execute(query.where(self._scope(db, s)).order_by(s.c.updated_at.desc(), s.c.id).limit(page_size).offset((page - 1) * page_size)).mappings()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def detail(self, session_id):
        s, m, r, p, t, c, n, a = (self.tables[name] for name in ("chat_sessions", "messages", "agent_runs", "prompt_versions", "agent_run_tools", "contacts", "session_notes", "audit_logs"))
        sid = self.key(s, session_id)
        with self.engine.connect() as db:
            self._guard(db)
            session = wire(db.execute(sa.select(s).where(s.c.id == sid, self._scope(db, s))).mappings().first())
            if not session:
                return None
            def rows(query):
                return [wire(row) for row in db.execute(query).mappings()]
            order = [m.c.sequence] if "sequence" in m.c else [m.c.created_at, sa.literal_column("messages.rowid")]
            messages = rows(sa.select(m).where(m.c.session_id == sid, m.c.status == "complete").order_by(*order))
            runs = rows(sa.select(r, p.c.version.label("prompt_version")).outerjoin(p, p.c.id == r.c.prompt_version_id).where(r.c.session_id == sid).order_by(r.c.started_at.desc(), r.c.id.desc()))
            tools = rows(sa.select(t.c.run_id, t.c.tool_name, t.c.status, t.c.started_at, t.c.finished_at).where(t.c.run_id.in_(sa.select(r.c.id).where(r.c.session_id == sid))).order_by(t.c.id))
            contacts = rows(sa.select(c).where(c.c.session_id == sid).order_by(c.c.created_at.desc(), c.c.id))
            notes = rows(sa.select(n).where(n.c.session_id == sid).order_by(n.c.created_at.desc(), n.c.id))
            audit = rows(sa.select(a).where(a.c.entity_type == "session", a.c.entity_id == str(session_id), self._record_scope(db, a)).order_by(a.c.created_at.desc(), a.c.id).limit(100))
        for run in runs:
            run["tools"] = [tool for tool in tools if tool["run_id"] == run["id"]]
        return {"session": session, "messages": messages, "runs": runs, "contacts": contacts, "notes": notes, "audit": audit}

    def add_note(self, session_id, content, now):
        s, n = self.tables["chat_sessions"], self.tables["session_notes"]
        with self.engine.begin() as db:
            principal = self._guard(db)
            self._lock_session(db, session_id)
            if not self._allowed(db, session_id):
                return None
            ownership = {"author_id": principal.user_id if principal else None, **self._tenant_fields(db, session_id)} if "author_id" in n.c else {}
            values = self.values(n, id=str(uuid.uuid4()), session_id=session_id, content=content.strip(), created_at=now, **ownership)
            db.execute(n.insert().values(**values))
            self._write_audit(db, action="session.note", entity_type="session", entity_id=session_id,
                              summary="添加内部备注", metadata={}, now=now)
            return wire(values)

    def overview(self):
        s, r, c, d, k, p, a = (self.tables[name] for name in ("chat_sessions", "agent_runs", "contacts", "documents", "knowledge_chunks", "provider_configs", "app_settings"))
        with self.engine.connect() as db:
            principal = self._guard(db)
            def count(table, *clauses):
                return db.execute(sa.select(sa.func.count()).select_from(table).where(*clauses)).scalar_one()
            scope = self._scope(db, s)
            run_scope = sa.exists(sa.select(s.c.id).where(s.c.id == r.c.session_id, scope))
            runs = count(r, run_scope)
            complete, errors = count(r, r.c.status == "complete", run_scope), count(r, r.c.status == "error", run_scope)
            recent = [wire(row) for row in db.execute(sa.select(r.c.id, r.c.session_id, r.c.error_code, r.c.provider, r.c.model, r.c.started_at).where(r.c.status == "error", run_scope).order_by(r.c.started_at.desc(), r.c.id.desc()).limit(8)).mappings()]
            scoped = {"sessions": count(s, s.c.deleted_at.is_(None), scope), "unresolved": count(s, s.c.deleted_at.is_(None), s.c.management_status != "resolved", scope),
                "runs": runs, "completion_rate": round(complete / runs * 100, 1) if runs else 0, "error_rate": round(errors / runs * 100, 1) if runs else 0,
                "contacts": count(c, self._record_scope(db, c)), "recent_errors": recent}
            if principal and "config.manage" not in PERMISSIONS[principal.role]:
                # Support staff get operational counts, not configuration status.
                return {**scoped, "documents": count(d, d.c.status == "ready", self._record_scope(db, d)),
                    "chunks": count(k, self._record_scope(db, k)), "providers": [], "active_provider": None}
            providers = [wire(row) for row in db.execute(sa.select(p.c.provider, p.c.model, p.c.encrypted_key.is_not(None).label("configured"), p.c.last_test_status, p.c.last_test_at).where(self._record_scope(db, p)).order_by(p.c.provider)).mappings()]
            return {**scoped, "documents": count(d, d.c.status == "ready", self._record_scope(db, d)), "chunks": count(k, self._record_scope(db, k)), "providers": providers,
                    "active_provider": db.execute(sa.select(a.c.value).where(a.c.key == "active_provider", self._record_scope(db, a))).scalar_one_or_none() or "deepseek"}

    def list_audit(self, page, page_size, action=None):
        a = self.tables["audit_logs"]
        clauses = [a.c.action.contains(action, autoescape=True)] if action else []
        return self._page(a, clauses, page, page_size)

    def _page(self, table, clauses, page, page_size):
        with self.engine.connect() as db:
            self._guard(db, "audit.read" if table.name == "audit_logs" else "contact.tenant")
            clauses = [*clauses, self._record_scope(db, table)]
            total = db.execute(sa.select(sa.func.count()).select_from(table).where(*clauses)).scalar_one()
            items = [wire(row) for row in db.execute(sa.select(table).where(*clauses).order_by(table.c.created_at.desc(), table.c.id).limit(page_size).offset((page - 1) * page_size)).mappings()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def create_contact(self, *, session_id, name, phone, email, summary, now):
        s, c = self.tables["chat_sessions"], self.tables["contacts"]
        with self.engine.begin() as db:
            principal = self._guard(db, "contact.own")
            parent = None
            if session_id:
                self._lock_session(db, session_id)
                parent = db.execute(sa.select(s).where(s.c.id == self.key(s, session_id), s.c.deleted_at.is_(None), self._scope(db, s))).mappings().first()
                if not parent:
                    return None
            contact_id = str(uuid.uuid4())
            ownership = {}
            if "tenant_id" in c.c:
                ownership = {"tenant_id": principal.tenant_id if principal else LEGACY_TENANT_ID,
                    "owner_id": parent["owner_id"] if parent else principal.user_id if principal else None}
            db.execute(c.insert().values(**self.values(c, id=contact_id, session_id=session_id, name=name, phone=phone, email=email, summary=summary, created_at=now, **ownership)))
            self._write_audit(db, action="contact.create", entity_type="contact", entity_id=contact_id,
                              summary="登记联系请求", metadata={}, now=now)
            return contact_id

    def list_contacts(self, q, status, page, page_size):
        c = self.tables["contacts"]
        clauses = []
        if q:
            clauses.append(sa.or_(*(c.c[name].contains(q, autoescape=True) for name in ("name", "phone", "email", "summary"))))
        if status:
            clauses.append(c.c.status == status)
        return self._page(c, clauses, page, page_size)

    def update_contact(self, contact_id, values):
        c = self.tables["contacts"]
        if not values or set(values) - {"status", "note"}:
            raise ValueError("Invalid contact update fields")
        if "status" in values and values["status"] not in {"new", "following", "closed"}:
            raise ValueError("Invalid contact status")
        with self.engine.begin() as db:
            self._guard(db, "contact.tenant")
            key = self.key(c, contact_id)
            result = db.execute(c.update().where(c.c.id == key, self._record_scope(db, c)).values(**self.values(c, **values)))
            if result.rowcount:
                self._write_audit(db, action="contact.update", entity_type="contact", entity_id=contact_id,
                    summary="更新联系记录", metadata={"fields": sorted(values)}, now=sa.func.now() if self.engine.dialect.name == "postgresql" else utc_now())
            return wire(db.execute(sa.select(c).where(c.c.id == key)).mappings().one()) if result.rowcount else None


class LocalAdminRepository(LocalRepository):
    repository_type = AdminRepository
