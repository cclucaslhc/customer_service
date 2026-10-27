"""Real PG/S3 scheduled source and archive acceptance; skips without real S3."""
import json
import sys
import uuid
from io import StringIO

import sqlalchemy as sa
import copy
import os
import hashlib
from datetime import datetime,timezone
import pytest

from app.backup_scheduler import BackupCatalog,BackupRunner
from app.backup_archive import fetch_package
from app.backup import restore_backup
from app.object_store import ObjectReference
from app.persistence.engine import build_engine
from app.persistence.schema import metadata
from app.storage_config import StoragePolicy,StorageTarget
from app.backup_verifier import LocalRestoreVerifier,ObjectRestoreTargets
from app.backup_catalog_archive import fetch_checkpoint
from app.backup_recovery_points import select_recovery_point
from app.backup import BackupError
from test_backup_scheduler import plan_for
from test_object_backup import object_backup,restore_store,backup_setup,postgres_database,real_s3,empty_database


def test_real_scheduled_objects_remote_receipt_fetch_and_restore(object_backup,tmp_path):
    bundle,common,package,tenant,source,client,payloads = object_backup
    with restore_store(client) as archive,restore_store(client) as target:
        namespace = str(uuid.uuid4())
        plan = plan_for(common,tmp_path,object_policy=source.policy_fingerprint,
            archive_policy=archive.policy_fingerprint,archive_namespace=namespace)
        BackupCatalog(bundle.engine).configure(plan)
        report = BackupRunner(bundle.engine,common['url'],plan,object_store=source,
            archive_store=archive,archive_namespace=namespace).run_once()
        assert report['objects'] == 2 and report['remote_verified'] and report['local_verified']
        receipt = plan.package_root/(report['attempt_id']+'.receipt.json')
        fetched = tmp_path/'fetched'
        result = fetch_package(destination=fetched,receipt=receipt,store=archive,namespace=namespace,
            key_path=plan.key_path,scratch_root=plan.scratch_root)
        assert result['backup_id'] == report['backup_id']
        with empty_database() as url:
            restored = restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-knowledge',
                'asset_root':tmp_path/'restored-assets'},source=fetched,isolated_confirmed=True,object_store=target)
            assert restored['verified'] and restored['objects_remapped'] == 2 and restored['recovery_hold']
            engine = build_engine(url,pool_size=1)
            try:
                with engine.connect() as db:
                    for name in ('documents','knowledge_assets'):
                        row = db.execute(sa.select(metadata.tables[name])).mappings().one()
                        ref = ObjectReference.from_dict(row['metadata_json']['_private_object'])
                        assert target.get(tenant,ref) == payloads[name]
                    assert not db.execute(sa.select(metadata.tables['backup_schedules'].c.enabled)).scalar_one()
            finally:
                engine.dispose()


def test_real_worker_explicit_role_credentials_and_remote_archive(object_backup,tmp_path,monkeypatch,capsys):
    import scripts.backup_worker as cli
    bundle,common,package,tenant,source,client,payloads = object_backup
    with restore_store(client) as archive:
        namespace = str(uuid.uuid4())
        plan = plan_for(common,tmp_path,object_policy=source.policy_fingerprint,
            archive_policy=archive.policy_fingerprint,archive_namespace=namespace)
        BackupCatalog(bundle.engine).configure(plan)
        policy = StoragePolicy([StorageTarget(role,'http://127.0.0.1:59000',store.bucket,'us-east-1')
            for role,store in (('source',source),('archive',archive))],loopback_tests=True)
        monkeypatch.setattr(StoragePolicy,'from_file',classmethod(lambda cls,path:policy))
        monkeypatch.setattr(cli,'load_plan',lambda path:plan)
        monkeypatch.setenv('BACKUP_DATABASE_URL',common['url'])
        monkeypatch.delenv('BACKUP_VERIFICATION_ADMIN_URL',raising=False)
        monkeypatch.setattr(sys,'stdin',StringIO(json.dumps({role:{'access_key':'synthetic-s3-access',
            'secret_key':'synthetic-s3-secret-only'} for role in ('source','archive')})))
        monkeypatch.setattr(sys,'argv',['backup_worker.py','--plan',str(tmp_path/'plan.json'),'--once',
            '--confirm-global-backup','--storage-policy',str(tmp_path/'policy.json'),
            '--source-target','source','--archive-target','archive','--storage-credentials-stdin'])
        assert cli.main() == 0
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report['objects'] == 2 and report['remote_verified']
        assert 'synthetic-s3-secret-only' not in captured.out+captured.err


def test_real_two_automatic_object_restores_have_fresh_buckets_and_hold(object_backup,tmp_path,monkeypatch):
    import app.backup_verifier as verification
    bundle,common,package,tenant,source,client,payloads = object_backup
    reserved=copy.copy(source); reserved.bucket='cs-verify-'+uuid.uuid4().hex[:12]
    targets=ObjectRestoreTargets(reserved,isolated_object_environment_confirmed=True)
    root=tmp_path/'verification'; root.mkdir()
    verifier=LocalRestoreVerifier(admin_url=os.environ['TEST_POSTGRES_URL'],root=root,
        isolated_environment_confirmed=True,object_targets=targets)
    plan=plan_for(common,tmp_path,object_policy=source.policy_fingerprint,restore_policy=verifier.policy_fingerprint)
    BackupCatalog(bundle.engine).configure(plan)
    buckets=[]; original=verification.restore_backup
    def checked(**kwargs):
        target=kwargs['object_store']; buckets.append(target.bucket)
        result=original(**kwargs)
        engine=build_engine(kwargs['url'],pool_size=1)
        try:
            with engine.connect() as db:
                assert db.execute(sa.select(metadata.tables['recovery_state'].c.blocked)).scalar_one()
                assert not db.execute(sa.select(metadata.tables['backup_schedules'].c.enabled)).scalar_one()
                for name in ('documents','knowledge_assets'):
                    row=db.execute(sa.select(metadata.tables[name])).mappings().one()
                    ref=ObjectReference.from_dict(row['metadata_json']['_private_object'])
                    assert ref.bucket == target.bucket and ref.bucket != source.bucket
                    assert target.get(tenant,ref) == payloads[name]
        finally:
            engine.dispose()
        return result
    monkeypatch.setattr(verification,'restore_backup',checked)
    try:
        runner=BackupRunner(bundle.engine,common['url'],plan,object_store=source,verifier=verifier)
        first=runner.run_once()
        with bundle.engine.begin() as db:
            db.execute(metadata.tables['backup_schedules'].update().values(next_due=sa.func.clock_timestamp()))
        second=runner.run_once()
        assert first['restore_verified'] and second['restore_verified']
        assert first['restore_bucket'] != second['restore_bucket']
        for report in (first,second):
            assert report['restore_bucket'] == reserved.bucket+'-'+uuid.UUID(report['attempt_id']).hex
            assert client.list_object_versions(Bucket=report['restore_bucket'])['Versions']
        with bundle.engine.connect() as db:
            for name in ('documents','knowledge_assets'):
                row=db.execute(sa.select(metadata.tables[name])).mappings().one()
                assert source.get(tenant,ObjectReference.from_dict(row['metadata_json']['_private_object'])) == payloads[name]
    finally:
        # Exact synthetic buckets captured for these attempts; never prefix-delete.
        for bucket in buckets:
            assert bucket.startswith(reserved.bucket+'-') and len(bucket) == len(reserved.bucket)+33
            result=client.list_object_versions(Bucket=bucket)
            assert not result.get('IsTruncated')
            refs=[{'Key':row['Key'],'VersionId':row['VersionId']} for row in result.get('Versions',[])+result.get('DeleteMarkers',[])]
            if refs:
                client.delete_objects(Bucket=bucket,Delete={'Objects':refs})
            client.delete_bucket(Bucket=bucket)


def test_real_independent_catalog_recovers_lost_local_receipt_then_actual_restore(object_backup,tmp_path):
    bundle,common,package,tenant,source,client,payloads=object_backup
    with restore_store(client) as archive,restore_store(client) as catalog,restore_store(client) as target:
        namespace=str(uuid.uuid4())
        plan=plan_for(common,tmp_path,object_policy=source.policy_fingerprint,archive_policy=archive.policy_fingerprint,
            archive_namespace=namespace,catalog_policy=catalog.policy_fingerprint)
        BackupCatalog(bundle.engine).configure(plan)
        report=BackupRunner(bundle.engine,common['url'],plan,object_store=source,archive_store=archive,
            archive_namespace=namespace,catalog_store=catalog).run_once()
        assert report['catalog_verified'] and report['remote_verified']
        offline=tmp_path/'offline'; offline.mkdir()
        anchor=offline/'anchor.json'
        anchor.write_bytes((plan.package_root/(report['attempt_id']+'.catalog.json')).read_bytes())
        (plan.package_root/(report['attempt_id']+'.receipt.json')).unlink()
        (plan.package_root/report['attempt_id']).rename(tmp_path/'unavailable-original-package')
        bundle.engine.dispose()  # Fetch has no source DB handle or API dependency.
        manifest=offline/'inventory.json'
        manifest.write_text(json.dumps({'format':1,'anchors':[{'path':str(anchor),
            'sha256':hashlib.sha256(anchor.read_bytes()).hexdigest()}]}))
        selection_options={'store':catalog,'namespace':namespace,'manifest_path':manifest,
            'expected_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'key_path':plan.key_path,'expected_schedule_id':plan.schedule_id,'expected_policy_fingerprint':plan.fingerprint,
            'not_after':datetime.now(timezone.utc),'max_gap_seconds':3600}
        with pytest.raises(BackupError,match='No approved'):
            select_recovery_point(**selection_options)  # No prior restore verifier in this plan.
        selection=select_recovery_point(**selection_options,require_restore=False)  # Explicit test waiver.
        assert selection['selected']['backup_id']==report['backup_id']
        checkpoint=tmp_path/'checkpoint'
        recovered=fetch_checkpoint(store=catalog,namespace=namespace,anchor_path=anchor,destination=checkpoint,
            expected_anchor_sha256=selection['selected']['anchor_sha256'],
            key_path=plan.key_path,scratch_root=plan.scratch_root,expected_schedule_id=plan.schedule_id,
            expected_policy_fingerprint=plan.fingerprint)
        assert recovered['backup_id']==report['backup_id'] and not recovered['workers_enabled']
        fetched=tmp_path/'fetched-from-checkpoint'
        fetch_package(receipt=checkpoint/'receipt.json',destination=fetched,store=archive,namespace=namespace,
            key_path=plan.key_path,scratch_root=plan.scratch_root)
        with empty_database() as url:
            restored=restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
                'asset_root':tmp_path/'restored-assets'},source=fetched,isolated_confirmed=True,object_store=target)
            assert restored['verified'] and restored['recovery_hold'] and restored['objects_remapped']==2
