"""Shared typed repositories and lazy local adapters; no credential discovery."""

import json
import threading
import uuid
from datetime import datetime

import sqlalchemy as sa

from app.persistence.schema import metadata


def wire(row):
    if row is None:
        return None
    return {key: (str(value) if isinstance(value, uuid.UUID) else value.isoformat() if isinstance(value, datetime) else value)
            for key, value in dict(row).items()}


class Repository:
    table_names = ()

    def __init__(self, engine):
        self.engine = engine
        if engine.dialect.name == "postgresql":
            self.tables = metadata.tables
        elif engine.dialect.name == "sqlite":
            local = sa.MetaData()
            local.reflect(engine, only=self.table_names)
            self.tables = local.tables
        else:
            raise ValueError("Unsupported database")

    def values(self, table, **values):
        result = {}
        for name, value in values.items():
            column = table.c[name]
            if value is not None:
                if isinstance(column.type, sa.Uuid):
                    value = uuid.UUID(str(value))
                elif isinstance(column.type, sa.DateTime) and isinstance(value, str):
                    value = datetime.fromisoformat(value)
                elif isinstance(column.type, sa.JSON) and isinstance(value, str):
                    value = json.loads(value)
                elif isinstance(column.type, sa.Text) and isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
            result[name] = value
        return result

    def key(self, table, value):
        return self.values(table, id=value)["id"]

    def serialize_writes(self, db, lock_id):
        """Take a transaction-scoped lock before read-modify-write operations.

        PostgreSQL advisory locks coordinate processes; SQLite's database write
        reservation is obtained before reading, not upgraded from a read later.
        Call only at the start of a fresh engine.begin() transaction.
        """
        if self.engine.dialect.name == "postgresql":
            db.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        else:
            db.exec_driver_sql("BEGIN IMMEDIATE")


class LocalRepository:
    """Lazy reflection follows the explicitly selected local database, including tests."""

    repository_type = Repository

    def __init__(self):
        self._repository = None
        self._path = None
        self._lock = threading.RLock()

    def _get(self):
        from app import database
        path = database.DATABASE_PATH.resolve()
        if self._repository is None or self._path != path:
            if self._repository:
                self._repository.engine.dispose()
            engine = sa.create_engine(sa.URL.create("sqlite", database=str(path)), poolclass=sa.pool.NullPool, hide_parameters=True,
                                      connect_args={"check_same_thread": False, "timeout": database.DATABASE_BUSY_TIMEOUT_MS / 1000})
            @sa.event.listens_for(engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA busy_timeout={database.DATABASE_BUSY_TIMEOUT_MS}")
            self._repository = self.repository_type(engine)
            self._path = path
        return self._repository

    def __getattr__(self, name):
        with self._lock:
            return getattr(self._get(), name)
