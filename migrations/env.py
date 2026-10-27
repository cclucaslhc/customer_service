"""Explicit migration command; never load the running app or local secrets."""

import os

from alembic import context
from app.persistence.engine import build_engine
from app.persistence.schema import metadata

url = os.environ.get("MIGRATION_DATABASE_URL")
if not url:
    raise RuntimeError("MIGRATION_DATABASE_URL must point to an explicitly selected database")

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = build_engine(url, pool_size=1)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata, compare_type=True)
            with context.begin_transaction():
                # Serialize independent migration runners for this database.
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(746219851)")
                context.run_migrations()
    finally:
        engine.dispose()
