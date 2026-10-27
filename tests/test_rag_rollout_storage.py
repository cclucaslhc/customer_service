"""Disposable PG storage safety only; not a functioning gray deployment."""
from datetime import datetime, timedelta, timezone
import uuid
import pytest
import sqlalchemy as sa
from app.backup import invalidate_restored_rollouts
from app.persistence.schema import metadata
from test_rag_release_service import release, queue_setup, postgres_database


def rollout(args):
    now = datetime.now(timezone.utc)
    return dict(tenant_id=uuid.UUID(args['tenant_id']), rollout_id=uuid.uuid4(),
        release_id=uuid.UUID(args['release_id']), plan={'scope': 'synthetic_storage_only'},
        plan_sha256='a'*64, created_at=now, expires_at=now+timedelta(minutes=10))


def test_rollout_definition_is_frozen_and_revision_is_cas(release):
    bundle, _, args, *_ = release
    table = metadata.tables['rag_rollouts']
    row = rollout(args)
    for changes in ({'plan': []}, {'plan_sha256': 'x'*64}, {'state': 'running'},
                    {'state': 'completed'}, {'basis_points': 10001}, {'step_index': 16}):
        with pytest.raises(sa.exc.IntegrityError), bundle.engine.begin() as db:
            db.execute(table.insert().values(**{**row, **changes}))
    with bundle.engine.begin() as db:
        db.execute(table.insert().values(**row))
    for changes in ({'plan': {'changed': True}, 'version': 1}, {'basis_points': 100, 'state': 'paused'}):
        with pytest.raises(sa.exc.DBAPIError), bundle.engine.begin() as db:
            db.execute(table.update().values(**changes))
    with bundle.engine.begin() as db:
        assert db.execute(table.update().where(table.c.version == 0).values(
            state='running', basis_points=100, step_index=0, version=1,step_started_at=sa.func.clock_timestamp())).rowcount == 1
        assert db.execute(table.update().where(table.c.version == 0).values(
            state='paused', version=1)).rowcount == 0


def test_one_live_rollout_per_tenant_and_restore_requires_review(release):
    bundle, _, args, *_ = release
    table, releases = metadata.tables['rag_rollouts'], metadata.tables['rag_release_intents']
    first = rollout(args)
    with bundle.engine.begin() as db:
        db.execute(table.insert().values(**{**first, 'state': 'running', 'basis_points': 100, 'step_index': 0,
            'step_started_at':first['created_at']}))
        original = dict(db.execute(sa.select(releases)).mappings().one())
        original['release_id'] = uuid.uuid4()
        db.execute(releases.insert().values(**original))
    second = {**first, 'rollout_id': uuid.uuid4(), 'release_id': original['release_id']}
    with pytest.raises(sa.exc.IntegrityError), bundle.engine.begin() as db:
        db.execute(table.insert().values(**second))
    with bundle.engine.begin() as db:
        assert invalidate_restored_rollouts(db) == 1
        assert invalidate_restored_rollouts(db) == 0
        recovered = db.execute(sa.select(table)).mappings().one()
        assert recovered['state'] == 'recovery_review' and recovered['basis_points'] == 0
        assert recovered['version'] == 1 and recovered['plan'] == first['plan']
    with pytest.raises(sa.exc.IntegrityError), bundle.engine.begin() as db:
        db.execute(table.insert().values(**second))


def test_rollout_foreign_tenant_reference_and_public_access_denied(release):
    bundle, _, args, *_ = release
    table = metadata.tables['rag_rollouts']
    row = rollout(args)
    with pytest.raises(sa.exc.IntegrityError), bundle.engine.begin() as db:
        db.execute(table.insert().values(**{**row, 'tenant_id': uuid.uuid4()}))
    role = 'cs_rollout_probe_'+uuid.uuid4().hex
    with bundle.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOINHERIT')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    try:
        for sql in ('SELECT * FROM rag_rollouts', 'INSERT INTO rag_rollouts DEFAULT VALUES',
                    'UPDATE rag_rollouts SET version=version+1 WHERE false', 'DELETE FROM rag_rollouts WHERE false'):
            with bundle.engine.begin() as db:
                db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                with pytest.raises(sa.exc.DBAPIError) as failure, db.begin_nested():
                    db.exec_driver_sql(sql)
                assert failure.value.orig.sqlstate == '42501'
    finally:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY "{role}"')
            db.exec_driver_sql(f'DROP ROLE "{role}"')
