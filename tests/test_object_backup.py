"""Encrypted object backup and restore on real disposable PostgreSQL + S3."""
from contextlib import contextmanager
import uuid

import pytest
import sqlalchemy as sa

from app.backup import BackupError, create_backup, restore_backup
from app.object_store import ObjectReference, ObjectStoreError, S3Credentials, S3ObjectStore
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.engine import build_engine
from app.persistence.schema import metadata
from test_logical_backup import backup_setup, empty_database, postgres_database
from test_object_store import real_s3


@pytest.fixture
def object_backup(backup_setup, real_s3):
    bundle, common, package, tenant, token, revoked = backup_setup
    store, client = real_s3
    payloads = {}
    with bundle.engine.begin() as db:
        for kind, name in (('knowledge', 'documents'), ('assets', 'knowledge_assets')):
            table = metadata.tables[name]
            for row in db.execute(sa.select(table)).mappings().all():
                source = common['knowledge_root' if kind == 'knowledge' else 'asset_root'] / tenant / row['stored_name']
                data = source.read_bytes()
                payloads[name] = data
                reference = store.put(tenant, data, kind=kind)
                db.execute(table.update().where(table.c.id == row['id']).values(
                    metadata_json={**row['metadata_json'], '_private_object':reference.to_dict()}))
                source.write_bytes(b'Synthetic stale cache must never be used')
    return bundle, common, package, tenant, store, client, payloads


@contextmanager
def restore_store(client):
    bucket = 'cs-test-s3-' + uuid.uuid4().hex
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status':'Enabled'})
    selected = S3ObjectStore(endpoint='http://127.0.0.1:59000', bucket=bucket, region='us-east-1',
        credentials=S3Credentials('synthetic-s3-access','synthetic-s3-secret-only'),
        allow_loopback=True, test_skip_public_block_api=True)
    try:
        yield selected
    finally:
        selected.close()
        result = client.list_object_versions(Bucket=bucket)
        objects = [{'Key':row['Key'], 'VersionId':row['VersionId']} for row in result.get('Versions', []) + result.get('DeleteMarkers', [])]
        assert not result.get('IsTruncated')
        if objects:
            client.delete_objects(Bucket=bucket, Delete={'Objects':objects})
        client.delete_bucket(Bucket=bucket)


def options(common, url, tmp_path):
    return {**common, 'url':url, 'knowledge_root':tmp_path / 'restored-docs', 'asset_root':tmp_path / 'restored-assets'}


def test_real_object_backup_restore_remaps_versions_without_source_or_cache(object_backup, tmp_path):
    bundle, common, package, tenant, source, client, payloads = object_backup
    versions = client.list_object_versions(Bucket=source.bucket)['Versions']
    for row in versions:
        client.put_object(Bucket=source.bucket, Key=row['Key'], Body=b'Synthetic new latest before backup')
    report = create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    assert report['files'] == 2 and report['objects'] == 2
    # Destroy exact source versions after backup: restore must use encrypted blobs.
    for row in versions:
        client.delete_object(Bucket=source.bucket, Key=row['Key'], VersionId=row['VersionId'])
    with empty_database() as url, restore_store(client) as target:
        report = restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)
        assert report['verified'] and report['objects_remapped'] == 2 and report['recovery_hold']
        restored = PostgreSQLRepositories.from_url(url, pool_size=1)
        try:
            with restored.engine.connect() as db:
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one()
                for kind, name in (('knowledge','documents'), ('assets','knowledge_assets')):
                    row = db.execute(sa.select(metadata.tables[name])).mappings().one()
                    ref = ObjectReference.from_dict(row['metadata_json']['_private_object'])
                    assert ref.bucket == target.bucket and ref.bucket != source.bucket
                    assert target.get(tenant, ref) == payloads[name]
            from app.recovery import RecoveryHold
            with pytest.raises(RecoveryHold):
                restored.check_schema()
            assert not list((tmp_path / 'restored-docs').rglob('*.txt'))
            assert not list((tmp_path / 'restored-assets').rglob('*.png'))
        finally:
            restored.close()


def test_real_corrupt_object_archive_aborts_before_upload_or_sql(object_backup, tmp_path):
    bundle, common, package, tenant, source, client, payloads = object_backup
    create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    blob = package / 'file-1.enc'
    data = bytearray(blob.read_bytes())
    data[-1] ^= 1
    blob.write_bytes(data)
    with empty_database() as url, restore_store(client) as target:
        with pytest.raises(BackupError, match='authentication'):
            restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)
        assert not client.list_object_versions(Bucket=target.bucket).get('Versions')
        engine = build_engine(url, pool_size=1)
        try:
            with engine.connect() as db:
                assert not sa.inspect(db).get_table_names()
        finally:
            engine.dispose()
        assert not (tmp_path / 'restored-docs').exists()


def test_real_missing_source_version_cannot_back_up_stale_local_cache(object_backup):
    bundle, common, package, tenant, source, client, payloads = object_backup
    row = client.list_object_versions(Bucket=source.bucket)['Versions'][0]
    client.delete_object(Bucket=source.bucket, Key=row['Key'], VersionId=row['VersionId'])
    with pytest.raises(ObjectStoreError):
        create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    assert not package.exists()


def test_real_restore_refuses_source_or_nonempty_target_bucket(object_backup, tmp_path):
    bundle, common, package, tenant, source, client, payloads = object_backup
    create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    with empty_database() as url:
        with pytest.raises(BackupError, match='differ'):
            restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=source)
        with restore_store(client) as target:
            ref = target.put(tenant, b'Synthetic existing object', kind='knowledge')
            with pytest.raises(ObjectStoreError, match='empty'):
                restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)
            client.delete_object(Bucket=target.bucket, Key=ref.key)
            client.delete_object(Bucket=target.bucket, Key=ref.key, VersionId=ref.version_id)
            remaining = client.list_object_versions(Bucket=target.bucket)
            assert remaining.get('DeleteMarkers') and not remaining.get('Versions')
            with pytest.raises(ObjectStoreError, match='empty'):
                restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)


def test_real_unknown_upload_ack_keeps_orphan_and_database_empty(object_backup, tmp_path, monkeypatch):
    bundle, common, package, tenant, source, client, payloads = object_backup
    create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    with empty_database() as url, restore_store(client) as target:
        original = target.put
        def lost_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise ObjectStoreError('Synthetic lost upload acknowledgement')
        monkeypatch.setattr(target, 'put', lost_ack)
        with pytest.raises(ObjectStoreError):
            restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)
        assert len(client.list_object_versions(Bucket=target.bucket)['Versions']) == 1
        engine = build_engine(url, pool_size=1)
        try:
            with engine.connect() as db:
                assert not sa.inspect(db).get_table_names()
        finally:
            engine.dispose()


def test_real_remapping_failure_rolls_back_all_references_and_keeps_hold(object_backup, tmp_path, monkeypatch):
    import app.backup as module
    bundle, common, package, tenant, source, client, payloads = object_backup
    create_backup(**common, destination=package, maintenance_confirmed=True, object_store=source)
    original = module._references
    with empty_database() as url, restore_store(client) as target:
        def reject_remapped(db, **kwargs):
            refs = original(db, **kwargs)
            if any(row.get('object', {}).get('bucket') == target.bucket for row in refs):
                raise BackupError('Synthetic mapping verification failure')
            return refs
        monkeypatch.setattr(module, '_references', reject_remapped)
        with pytest.raises(BackupError, match='mapping verification'):
            restore_backup(**options(common, url, tmp_path), source=package, isolated_confirmed=True, object_store=target)
        engine = build_engine(url, pool_size=1)
        try:
            with engine.connect() as db:
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one()
                assert all(row['object']['bucket'] == source.bucket for row in original(db, include_objects=True))
        finally:
            engine.dispose()
        assert len(client.list_object_versions(Bucket=target.bucket)['Versions']) == 2
