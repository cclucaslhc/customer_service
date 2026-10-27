"""Own disposable PostgreSQL only: migration and immutable-image Worker CLI."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from app.knowledge import KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.engine import build_engine


# Fixed synthetic credentials, not a configurable production database loader.
url = 'postgresql+psycopg://integration:local-integration-only@127.0.0.1:5432/postgres'
engine = build_engine(url, pool_size=1)
with engine.begin() as db:
    db.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector')
engine.dispose()
os.environ['MIGRATION_DATABASE_URL'] = url
command.upgrade(Config('/opt/customer-service/alembic.ini'), 'head')
bundle = PostgreSQLRepositories.from_url(url, pool_size=2)
try:
    bundle.check_schema()
    tenant = bundle.identity.create_tenant('Synthetic immutable image acceptance')
    bundle.identity.provision_user('synthetic.image.admin', 'synthetic-password-only', tenant, role='admin')
    token = bundle.identity.login('synthetic.image.admin', 'synthetic-password-only', tenant).token
    with tempfile.TemporaryDirectory(prefix='cs-image-knowledge-') as root:
        paths = {key:Path(root)/name for key,name in (
            ('knowledge_dir','docs'),('asset_dir','assets'),('legacy_upload_dir','legacy'))}
        service = KnowledgeService(repository=bundle.knowledge.authenticated(token), **paths)
        jobs = KnowledgeJobService(service, durable=True)
        def run_worker():
            result = subprocess.run([sys.executable, 'scripts/knowledge_worker.py',
                '--database-url', url, '--knowledge-root', str(paths['knowledge_dir']),
                '--asset-root', str(paths['asset_dir']), '--legacy-root', str(paths['legacy_upload_dir']),
                '--once'], capture_output=True, timeout=60)
            assert result.returncode == 0, result.stderr.decode(errors='replace')
        upload = jobs.create_upload('synthetic.txt', b'Synthetic immutable image queued document.')
        run_worker()
        assert jobs.get(upload['id'])['status'] == 'complete'
        document = service.list_documents()[0]
        assert document['version'] == 1
        reparse = jobs.create_reparse(document['id'])
        run_worker()
        assert jobs.get(reparse['id'])['status'] == 'complete'
        assert service.list_documents()[0]['version'] == 2
        cancelled = jobs.create_upload('cancelled.txt', b'Synthetic image cancelled task.')
        assert jobs.cancel(cancelled['id'])['status'] == 'cancelled'
        run_worker()
        assert len(service.list_documents()) == 1
        with bundle.engine.connect() as db:
            assert db.execute(sa.text('SELECT count(*) FROM knowledge_jobs WHERE execution_slot_held')).scalar_one() == 0
        print('immutable image migration, CLI upload/reparse/cancel and slot release passed')
finally:
    bundle.close()
