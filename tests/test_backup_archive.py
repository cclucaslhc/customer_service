"""Encrypted remote package lifecycle on real disposable PostgreSQL and S3."""
import io
import json
from pathlib import Path
import uuid
import sys

import pytest
import sqlalchemy as sa

import app.backup_archive as archives
from app.backup import BackupError, create_backup, restore_backup, _decrypt, _key
from app.backup_archive import archive_package, fetch_package
from app.backup_storage import operator_store
from app.object_store import ObjectReference, ObjectStoreError
from app.storage_config import StoragePolicy
from app.persistence.engine import build_engine
from app.persistence.schema import metadata
from test_object_backup import object_backup, restore_store, options
from test_logical_backup import backup_setup, postgres_database, empty_database
from test_object_store import real_s3


def archive_options(common,tmp_path,store):
    return {'receipt':tmp_path/'receipt.json','store':store,'namespace':str(uuid.uuid4()),
            'key_path':common['key_path'],'scratch_root':tmp_path}


def decoded_index(args,tmp_path):
    receipt = json.loads(args['receipt'].read_text())
    blob = tmp_path/'index.enc'
    blob.write_bytes(args['store'].get(args['namespace'],ObjectReference.from_dict(receipt['index'])))
    plain = tmp_path/'index.json'
    _decrypt(blob,plain,_key(args['key_path']),'remote-index:'+receipt['archive_id'])
    return receipt,json.loads(plain.read_text())


def test_real_archive_fetch_exact_versions_and_restore_database_objects(object_backup,tmp_path):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        result = archive_package(package=package,**args)
        assert result['remote_verified'] and result['files'] == 5
        receipt,index = decoded_index(args,tmp_path)
        assert b'Synthetic retained knowledge' not in archive_store.get(args['namespace'],ObjectReference.from_dict(receipt['index']))
        # Later latest objects must not change the authenticated archive receipt.
        for ref in [receipt['index'],*(obj for entry in index['files'] for obj in entry['chunks'])]:
            client.put_object(Bucket=archive_store.bucket,Key=ref['key'],Body=b'Synthetic corrupt new latest')
        old = tmp_path/'retired-original'
        package.rename(old)
        fetched = tmp_path/'fetched'
        result = fetch_package(destination=fetched,**args)
        assert result['verified'] and not result['workers_enabled']
        assert {p.name:p.read_bytes() for p in fetched.iterdir()} == {p.name:p.read_bytes() for p in old.iterdir()}
        with empty_database() as url,restore_store(client) as recovered_store:
            report = restore_backup(**options(common,url,tmp_path),source=fetched,isolated_confirmed=True,object_store=recovered_store)
            assert report['verified'] and report['recovery_hold'] and report['objects_remapped'] == 2
            restored = build_engine(url,pool_size=1)
            try:
                with restored.connect() as db:
                    assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one()
                    for name in ('documents','knowledge_assets'):
                        row = db.execute(sa.select(metadata.tables[name])).mappings().one()
                        ref = ObjectReference.from_dict(row['metadata_json']['_private_object'])
                        assert recovered_store.get(tenant,ref) == payloads[name]
            finally:
                restored.dispose()


def test_real_archive_corruption_or_inventory_rejected_before_upload(object_backup,tmp_path,monkeypatch):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        monkeypatch.setattr(archive_store,'put',lambda *a,**kw:pytest.fail('Invalid package uploaded'))
        (package/'unexpected.txt').write_bytes(b'Synthetic unreferenced file')
        with pytest.raises(BackupError,match='inventory'):
            archive_package(package=package,**args)
        (package/'unexpected.txt').unlink()
        blob = package/'file-1.enc'
        data = bytearray(blob.read_bytes()); data[-1] ^= 1; blob.write_bytes(data)
        with pytest.raises(BackupError,match='authentication'):
            archive_package(package=package,**args)
        assert not args['receipt'].exists()


def test_real_missing_chunk_refuses_latest_and_never_publishes_package(object_backup,tmp_path):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        archive_package(package=package,**args)
        receipt,index = decoded_index(args,tmp_path)
        ref = index['files'][0]['chunks'][0]
        client.put_object(Bucket=archive_store.bucket,Key=ref['key'],Body=b'Synthetic available latest')
        client.delete_object(Bucket=archive_store.bucket,Key=ref['key'],VersionId=ref['version_id'])
        with pytest.raises(ObjectStoreError):
            fetch_package(destination=tmp_path/'fetched',**args)
        assert not (tmp_path/'fetched').exists()


def test_real_lost_index_upload_ack_keeps_remote_versions_without_receipt(object_backup,tmp_path,monkeypatch):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        original,calls = archive_store.put,[]
        def lost_ack(*a,**kw):
            ref = original(*a,**kw); calls.append(ref)
            if len(calls) == 6:
                raise ObjectStoreError('Synthetic missing index acknowledgement')
            return ref
        monkeypatch.setattr(archive_store,'put',lost_ack)
        with pytest.raises(ObjectStoreError):
            archive_package(package=package,**args)
        assert len(client.list_object_versions(Bucket=archive_store.bucket)['Versions']) == 6
        assert not args['receipt'].exists()


def test_real_receipt_binding_no_overwrite_and_chunk_budget(object_backup,tmp_path,monkeypatch):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        monkeypatch.setattr(archives,'MAX_CHUNKS',1)
        with pytest.raises(BackupError,match='budget'):
            archive_package(package=package,**args)
        assert not client.list_object_versions(Bucket=archive_store.bucket).get('Versions')
        monkeypatch.setattr(archives,'MAX_CHUNKS',8192)
        archive_package(package=package,**args)
        original = args['receipt'].read_bytes()
        with pytest.raises(BackupError,match='new receipt'):
            archive_package(package=package,**args)
        assert args['receipt'].read_bytes() == original
        altered = json.loads(original); altered['archive_id'] = str(uuid.uuid4())
        args['receipt'].write_text(json.dumps(altered))
        with pytest.raises(BackupError,match='authentication'):
            fetch_package(destination=tmp_path/'fetched',**args)
        assert not (tmp_path/'fetched').exists()
        args['receipt'].write_bytes(original)
        dest = tmp_path/'existing'; dest.mkdir(); (dest/'kept.txt').write_bytes(b'Synthetic existing artifact')
        with pytest.raises(BackupError,match='new destination'):
            fetch_package(destination=dest,**args)
        assert (dest/'kept.txt').read_bytes() == b'Synthetic existing artifact'


def test_real_package_change_during_upload_does_not_publish_receipt(object_backup,tmp_path,monkeypatch):
    bundle,common,package,tenant,source,client,payloads = object_backup
    create_backup(**common,destination=package,maintenance_confirmed=True,object_store=source)
    with restore_store(client) as archive_store:
        args = archive_options(common,tmp_path,archive_store)
        original,calls = archive_store.put,[]
        def changed(*a,**kw):
            ref = original(*a,**kw); calls.append(ref)
            if len(calls) == 1:
                blob = package/'file-1.enc'
                data = bytearray(blob.read_bytes()); data[-1] ^= 1; blob.write_bytes(data)
            return ref
        monkeypatch.setattr(archive_store,'put',changed)
        with pytest.raises(BackupError,match='changed during archive'):
            archive_package(package=package,**args)
        assert not args['receipt'].exists()
        assert client.list_object_versions(Bucket=archive_store.bucket).get('Versions')


def test_explicit_operator_credentials_are_bounded_and_never_echoed(tmp_path,monkeypatch):
    path = tmp_path/'policy.json'
    path.write_text(json.dumps([{'name':'approved','endpoint':'https://objects.example.invalid',
        'bucket':'synthetic-private','region':'us-east-1'}]))
    observed = []
    monkeypatch.setattr(StoragePolicy,'build',lambda self,target,credentials:observed.append((target,credentials)) or object())
    for raw in ('synthetic-sensitive-plaintext',' '*16385,json.dumps({'access_key':'synthetic',
        'secret_key':'synthetic-only','endpoint':'https://unexpected.invalid'})):
        with pytest.raises(BackupError) as failure:
            operator_store(policy_path=path,target_name='approved',credential_stream=io.StringIO(raw))
        assert raw not in str(failure.value)
    assert not observed
    operator_store(policy_path=path,target_name='approved',credential_stream=io.StringIO(json.dumps({
        'access_key':'synthetic-only','secret_key':'synthetic-only','session_token':'synthetic-only'})))
    assert observed[0][0].bucket == 'synthetic-private'
    assert 'synthetic-only' not in repr(observed[0][1])


def test_real_logical_backup_cli_supplies_independent_credentials_and_closes(object_backup,tmp_path,monkeypatch,capsys):
    import scripts.logical_backup as cli
    from app.storage_config import StorageTarget
    bundle,common,package,tenant,source,client,payloads = object_backup
    # Trusted isolated test DI only; production policy loader has no HTTP exemption.
    policy = StoragePolicy([StorageTarget('approved','http://127.0.0.1:59000',source.bucket,'us-east-1')],loopback_tests=True)
    monkeypatch.setattr(StoragePolicy,'from_file',classmethod(lambda cls,path:policy))
    closed = []
    real_builder = cli.operator_store
    def build(**kwargs):
        store = real_builder(**kwargs)
        original = store.close
        def close():
            closed.append(True); original()
        store.close = close
        return store
    monkeypatch.setattr(cli,'operator_store',build)
    monkeypatch.setenv('BACKUP_DATABASE_URL',common['url'])
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'access_key':'synthetic-s3-access','secret_key':'synthetic-s3-secret-only'})))
    monkeypatch.setattr(sys,'argv',['logical_backup.py','create','--binary-dir',str(common['binary_dir']),
        '--package',str(package),'--knowledge-root',str(common['knowledge_root']),'--asset-root',str(common['asset_root']),
        '--key-path',str(common['key_path']),'--scratch-root',str(tmp_path),'--confirm-offline-maintenance',
        '--storage-policy',str(tmp_path/'explicit-policy.json'),'--storage-target','approved','--storage-credentials-stdin'])
    assert cli.main() == 0 and closed == [True]
    output = capsys.readouterr()
    assert json.loads(output.out)['objects'] == 2
    assert 'synthetic-s3-secret-only' not in output.out+output.err


def test_cli_rejects_partial_config_before_credential_input_and_has_no_key_echo(tmp_path,monkeypatch,capsys):
    import scripts.logical_backup as cli
    monkeypatch.setenv('BACKUP_DATABASE_URL','postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic')
    monkeypatch.setattr(cli,'operator_store',lambda **kwargs:pytest.fail('Partial configuration requested credentials'))
    monkeypatch.setattr(sys,'argv',['logical_backup.py','create','--binary-dir',str(tmp_path),
        '--package',str(tmp_path/'package'),'--knowledge-root',str(tmp_path),'--asset-root',str(tmp_path),
        '--key-path',str(tmp_path/'key'),'--scratch-root',str(tmp_path),'--storage-target','approved'])
    assert cli.main() == 1
    output = capsys.readouterr()
    assert not output.out and 'synthetic-only' not in output.err


def test_archive_cli_refuses_implicit_credentials(tmp_path,monkeypatch,capsys):
    import scripts.backup_archive as cli
    monkeypatch.setattr(cli,'operator_store',lambda **kwargs:pytest.fail('Implicit credentials were requested'))
    monkeypatch.setattr(sys,'argv',['backup_archive.py','fetch','--package',str(tmp_path/'fetched'),
        '--receipt',str(tmp_path/'receipt.json'),'--key-path',str(tmp_path/'key'),'--scratch-root',str(tmp_path),
        '--namespace',str(uuid.uuid4()),'--storage-policy',str(tmp_path/'policy.json'),'--storage-target','approved'])
    assert cli.main() == 1
    assert not capsys.readouterr().out
