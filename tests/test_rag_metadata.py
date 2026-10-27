"""Index publication must not silently activate two tenant versions."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.persistence.rag_metadata_v1 import add_rag_metadata


def test_index_manifest_requires_ready_hash_and_single_active_version():
    meta = sa.MetaData()
    tenants = sa.Table('identity_tenants', meta, sa.Column('id', sa.Uuid(), primary_key=True))
    profiles, indexes = add_rag_metadata(meta)
    engine = sa.create_engine('sqlite://')
    sa.event.listen(engine, 'connect', lambda connection, _: connection.execute('PRAGMA foreign_keys=ON'))
    meta.create_all(engine)
    tenant_id, profile_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as db:
        db.execute(tenants.insert().values(id=tenant_id))
        db.execute(profiles.insert().values(tenant_id=tenant_id, profile_id=profile_id,
            model_id='synthetic-local', model_revision='fixed-test-revision',
            weights_sha256='a' * 64, license_id='synthetic', dimension=3,
            distance='cosine', normalized=True, max_tokens=128, created_at=now))
        common = dict(tenant_id=tenant_id, profile_id=profile_id,
            lexical_version='test', chunker_version='test', created_at=now)
        db.execute(indexes.insert().values(**common, index_id=uuid4(), status='building'))
        db.execute(indexes.insert().values(**common, index_id=uuid4(), status='active',
            manifest_sha256='b' * 64, activated_at=now))
        with pytest.raises(sa.exc.IntegrityError):
            with db.begin_nested():
                db.execute(indexes.insert().values(**common, index_id=uuid4(), status='active',
                    manifest_sha256='c' * 64, activated_at=now))
        with pytest.raises(sa.exc.IntegrityError):
            with db.begin_nested():
                db.execute(indexes.insert().values(**common, index_id=uuid4(), status='ready'))
    engine.dispose()
