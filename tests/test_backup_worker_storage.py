"""Offline credential/CLI boundaries, not evidence of real remote storage."""
from dataclasses import replace
from contextlib import contextmanager
from io import StringIO
import json
from types import SimpleNamespace
import sys
import uuid

import pytest
import sqlalchemy as sa

from app.backup import BackupError
from app.backup_storage import operator_stores
from app.backup_scheduler import BackupPlan,BackupRunner
from app.object_store import S3ObjectStore,S3Credentials
from app.storage_config import StoragePolicy
from app.persistence.schema import SCHEMA_REVISION


def store(bucket='synthetic-source',**options):
    return S3ObjectStore(endpoint='https://objects.example.invalid',bucket=bucket,region='us-east-1',
        credentials=S3Credentials('synthetic-access','synthetic-only'),client=object(),**options)


def plan(tmp_path,**options):
    return BackupPlan(schedule_id=uuid.uuid4(),interval_seconds=60,binary_dir=tmp_path,
        knowledge_root=tmp_path,asset_root=tmp_path,package_root=tmp_path,key_path=tmp_path/'key',
        scratch_root=tmp_path,**options)


def policy_file(tmp_path):
    path = tmp_path/'policy.json'
    path.write_text(json.dumps([{'name':name,'endpoint':'https://objects.example.invalid',
        'bucket':'synthetic-'+name,'region':'us-east-1'} for name in ('source','archive')]))
    return path


def envelope(*roles):
    return json.dumps({role:{'access_key':'synthetic-access-'+role,'secret_key':'synthetic-only-'+role}
        for role in roles})


def test_storage_fingerprint_binds_destination_not_credentials():
    first = store()
    same = S3ObjectStore(endpoint='https://objects.example.invalid/',bucket=first.bucket,region='us-east-1',
        credentials=S3Credentials('rotated-synthetic','rotated-synthetic-only'),client=object())
    assert first.policy_fingerprint == same.policy_fingerprint
    assert first.policy_fingerprint != store('synthetic-archive').policy_fingerprint


@pytest.mark.parametrize('raw',(' '*32769,'synthetic-sensitive',envelope('source'),envelope('source','archive','extra')),
    ids=('oversize','non-json','missing-role','extra-role'))
def test_envelope_requires_exact_roles_before_any_client_build(tmp_path,monkeypatch,raw):
    monkeypatch.setattr(StoragePolicy,'build',lambda *a,**k:pytest.fail('Invalid envelope constructed client'))
    with pytest.raises(BackupError) as failure:
        operator_stores(policy_path=policy_file(tmp_path),targets={'source':'source','archive':'archive'},
            credential_stream=StringIO(raw))
    assert raw not in str(failure.value)


def test_partial_client_construction_closes_only_owned_clients(tmp_path,monkeypatch):
    closed = []
    def build(self,target,credentials):
        if target.name == 'archive':
            raise RuntimeError('synthetic-sensitive SDK error')
        return SimpleNamespace(close=lambda:closed.append(target.name))
    monkeypatch.setattr(StoragePolicy,'build',build)
    with pytest.raises(BackupError,match='construction failed') as failure:
        operator_stores(policy_path=policy_file(tmp_path),targets={'source':'source','archive':'archive'},
            credential_stream=StringIO(envelope('source','archive')))
    assert closed == ['source'] and 'synthetic-sensitive' not in str(failure.value)


def test_runner_cannot_omit_replace_or_add_storage_policy(tmp_path):
    source,archive = store(),store('synthetic-archive')
    namespace = str(uuid.uuid4())
    approved = plan(tmp_path,object_policy=source.policy_fingerprint,
        archive_policy=archive.policy_fingerprint,archive_namespace=namespace)
    url = 'postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic'
    engine = sa.create_engine(url)
    try:
        with pytest.raises(ValueError,match='destination differs'):
            BackupRunner(engine,url,approved)
        with pytest.raises(ValueError,match='destination differs'):
            BackupRunner(engine,url,approved,object_store=archive,archive_store=archive,archive_namespace=namespace)
        with pytest.raises(ValueError,match='namespace differs'):
            BackupRunner(engine,url,approved,object_store=source,archive_store=archive,archive_namespace=str(uuid.uuid4()))
        with pytest.raises(ValueError,match='destination differs'):
            BackupRunner(engine,url,plan(tmp_path),object_store=source)
        BackupRunner(engine,url,approved,object_store=source,archive_store=archive,archive_namespace=namespace)
        with pytest.raises(ValueError,match='required together'):
            replace(approved,archive_namespace=None).validate()
    finally:
        engine.dispose()


def test_worker_supplies_both_roles_and_closes_after_execution_failure(tmp_path,monkeypatch,capsys):
    import scripts.backup_worker as cli
    source,archive = store(),store('synthetic-archive')
    selected = {'source':source,'archive':archive}
    closed,observed = [],[]
    for role,value in selected.items():
        value.close = lambda role=role:closed.append(role)
        value.check_ready = lambda:None
    approved = plan(tmp_path,object_policy=source.policy_fingerprint,archive_policy=archive.policy_fingerprint,
        archive_namespace=str(uuid.uuid4()))
    monkeypatch.setattr(cli,'load_plan',lambda path:approved)
    monkeypatch.setattr(StoragePolicy,'build',lambda self,target,credentials:selected[target.name])
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *a,**k:SimpleNamespace(scalar_one=lambda:SCHEMA_REVISION))
    fake_engine = SimpleNamespace(connect=connection,dispose=lambda:observed.append('disposed'))
    monkeypatch.setattr(cli,'build_engine',lambda *a,**k:fake_engine)
    def runner(engine,url,plan,**kwargs):
        assert kwargs['object_store'] is source and kwargs['archive_store'] is archive
        assert kwargs['archive_namespace'] == approved.archive_namespace
        observed.append('runner')
        def failed():
            raise RuntimeError('synthetic-only secret must not be echoed')
        return SimpleNamespace(run_once=failed)
    monkeypatch.setattr(cli,'BackupRunner',runner)
    monkeypatch.setenv('BACKUP_DATABASE_URL','postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic')
    monkeypatch.delenv('BACKUP_VERIFICATION_ADMIN_URL',raising=False)
    monkeypatch.setattr(sys,'stdin',StringIO(envelope('source','archive')))
    monkeypatch.setattr(sys,'argv',['backup_worker.py','--plan',str(tmp_path/'plan.json'),'--once',
        '--confirm-global-backup','--storage-policy',str(policy_file(tmp_path)),
        '--source-target','source','--archive-target','archive','--storage-credentials-stdin'])
    assert cli.main() == 1 and closed == ['source','archive'] and observed == ['runner','disposed']
    output = capsys.readouterr()
    assert not output.out and 'synthetic-only' not in output.err


def test_worker_missing_storage_role_refuses_before_credentials(tmp_path,monkeypatch,capsys):
    import scripts.backup_worker as cli
    approved = plan(tmp_path,object_policy=store().policy_fingerprint)
    monkeypatch.setattr(cli,'load_plan',lambda path:approved)
    monkeypatch.setattr(cli,'operator_stores',lambda **k:pytest.fail('Missing role requested credentials'))
    monkeypatch.setenv('BACKUP_DATABASE_URL','postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic')
    monkeypatch.setattr(sys,'argv',['backup_worker.py','--plan',str(tmp_path/'plan.json'),'--once','--confirm-global-backup'])
    assert cli.main() == 1 and not capsys.readouterr().out


def test_worker_supplies_isolated_verification_role_without_provisioning_during_setup(tmp_path,monkeypatch,capsys):
    import scripts.backup_worker as cli
    from app.backup_verifier import ObjectRestoreTargets,LocalRestoreVerifier
    source,verification = store(),store('cs-verify')
    admin_url='postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic'
    expected=LocalRestoreVerifier(admin_url=admin_url,root=tmp_path,isolated_environment_confirmed=True,
        object_targets=ObjectRestoreTargets(verification,isolated_object_environment_confirmed=True))
    approved=plan(tmp_path,object_policy=source.policy_fingerprint,restore_policy=expected.policy_fingerprint)
    closed=[]
    selected={'source':source,'verification':verification}
    source.check_ready=lambda:None
    verification.check_ready=lambda:pytest.fail('Reserved prefix treated as existing bucket')
    for role,value in selected.items():
        value.close=lambda role=role:closed.append(role)
    path=tmp_path/'policy.json'
    path.write_text(json.dumps([{'name':role,'endpoint':'https://objects.example.invalid',
        'bucket':value.bucket,'region':'us-east-1'} for role,value in selected.items()]))
    monkeypatch.setattr(cli,'load_plan',lambda path:approved)
    monkeypatch.setattr(StoragePolicy,'build',lambda self,target,credentials:selected[target.name])
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *a,**k:SimpleNamespace(scalar_one=lambda:SCHEMA_REVISION))
    monkeypatch.setattr(cli,'build_engine',lambda *a,**k:SimpleNamespace(connect=connection,dispose=lambda:None))
    def runner(engine,url,plan,**kwargs):
        assert kwargs['verifier'].policy_fingerprint == expected.policy_fingerprint
        assert kwargs['verifier'].object_targets.template is verification
        return SimpleNamespace(run_once=lambda:None)
    monkeypatch.setattr(cli,'BackupRunner',runner)
    monkeypatch.setenv('BACKUP_DATABASE_URL',admin_url)
    monkeypatch.setenv('BACKUP_VERIFICATION_ADMIN_URL',admin_url)
    monkeypatch.setattr(sys,'stdin',StringIO(envelope('source','verification')))
    monkeypatch.setattr(sys,'argv',['backup_worker.py','--plan',str(tmp_path/'plan.json'),'--once',
        '--confirm-global-backup','--storage-policy',str(path),'--source-target','source',
        '--storage-credentials-stdin','--verification-object-target','verification',
        '--verification-root',str(tmp_path),'--confirm-isolated-verification','--confirm-isolated-object-verification'])
    assert cli.main() == 0 and closed == ['source','verification']
    assert json.loads(capsys.readouterr().out) == {'claimed':False}


def test_catalog_cannot_share_archive_destination_or_be_omitted_from_runner(tmp_path):
    archive,catalog=store('synthetic-archive'),store('synthetic-catalog')
    namespace=str(uuid.uuid4())
    approved=plan(tmp_path,archive_policy=archive.policy_fingerprint,archive_namespace=namespace,
        catalog_policy=catalog.policy_fingerprint)
    with pytest.raises(ValueError,match='distinct'):
        replace(approved,catalog_policy=archive.policy_fingerprint).validate()
    url='postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic'
    engine=sa.create_engine(url)
    try:
        with pytest.raises(ValueError,match='destination differs'):
            BackupRunner(engine,url,approved,archive_store=archive,archive_namespace=namespace)
        BackupRunner(engine,url,approved,archive_store=archive,archive_namespace=namespace,catalog_store=catalog)
    finally:
        engine.dispose()
