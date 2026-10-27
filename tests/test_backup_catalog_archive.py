"""Catalog cryptography/fault injection; not evidence of actual S3 semantics."""
import hashlib
import json
from types import SimpleNamespace
import uuid
import os
import sys

import pytest
from app.backup import BackupError,_encrypt
from app.backup_catalog_archive import publish_checkpoint,fetch_checkpoint
from app.backup_scheduler import BackupAttempt
from app.object_store import ObjectReference,ObjectStoreError


class MemoryStore:
    def __init__(self,bucket):
        self.bucket=bucket; self.data={}; self.fail=False
    def put(self,namespace,data,kind):
        digest=hashlib.sha256(data).hexdigest()
        ref=ObjectReference(self.bucket,f'tenants/{namespace}/backup/{uuid.uuid4().hex}/{digest}','synthetic-v1',len(data),digest)
        self.data[ref.key]=data
        if self.fail:
            raise ObjectStoreError('Synthetic unknown write acknowledgement')
        return ref
    def get(self,namespace,ref):
        if ref.bucket!=self.bucket or not ref.key.startswith(f'tenants/{namespace}/backup/'):
            raise ObjectStoreError('Synthetic binding mismatch')
        data=self.data[ref.key]
        if len(data)!=ref.size or hashlib.sha256(data).hexdigest()!=ref.sha256:
            raise ObjectStoreError('Synthetic integrity mismatch')
        return data


@pytest.fixture
def checkpoint(tmp_path):
    namespace=str(uuid.uuid4()); archive_id=str(uuid.uuid4()); backup_id=str(uuid.uuid4())
    key_path=tmp_path/'key'; key_path.write_bytes(os.urandom(32))
    archive,vault=MemoryStore('synthetic-archive'),MemoryStore('synthetic-catalog')
    report={'backup_id':backup_id,'schema':'0018_backup_schedule','files':2,'objects':0,
        'snapshot_at':'2026-10-07T00:00:00+00:00','elapsed_seconds':1,'local_verified':True,
        'archive_id':archive_id,'remote_verified':True,'chunks':5}
    plain=tmp_path/'index.json'; encrypted=tmp_path/'index.enc'
    plain.write_text(json.dumps({'format':1,'namespace':namespace,'archive_id':archive_id,
        'backup_id':backup_id,'snapshot_at':report['snapshot_at']}))
    _encrypt(plain,encrypted,key_path.read_bytes(),'remote-index:'+archive_id)
    reference=archive.put(namespace,encrypted.read_bytes(),kind='backup')
    receipt=tmp_path/'receipt.json'
    receipt.write_text(json.dumps({'format':1,'namespace':namespace,'archive_id':archive_id,'index':reference.to_dict()}))
    plan=SimpleNamespace(schedule_id=uuid.uuid4(),fingerprint='a'*64)
    attempt=BackupAttempt(uuid.uuid4(),uuid.uuid4(),1)
    return {'store':vault,'archive_store':archive,'namespace':namespace,'plan':plan,'attempt':attempt,
        'report':report,'receipt_path':receipt,'anchor_path':tmp_path/'anchor.json',
        'key_path':key_path,'scratch_root':tmp_path,'guard':lambda:None}


def fetched(options,tmp_path,**changes):
    return fetch_checkpoint(**{'store':options['store'],'namespace':options['namespace'],
        'anchor_path':options['anchor_path'],'destination':tmp_path/'fetched','key_path':options['key_path'],
        'scratch_root':tmp_path,'expected_schedule_id':options['plan'].schedule_id,
        'expected_policy_fingerprint':options['plan'].fingerprint,**changes})


def test_checkpoint_preserves_receipt_without_source_db_and_has_no_plaintext_remote(checkpoint,tmp_path):
    result=publish_checkpoint(**checkpoint)
    assert result['catalog_verified']
    wire=next(iter(checkpoint['store'].data.values()))
    assert checkpoint['report']['backup_id'].encode() not in wire
    checkpoint['receipt_path'].unlink()  # Simulated loss of source-local receipt.
    recovery=fetched(checkpoint,tmp_path)
    assert recovery['verified'] and not recovery['workers_enabled']
    receipt=json.loads((tmp_path/'fetched'/'receipt.json').read_text())
    assert receipt['archive_id']==checkpoint['report']['archive_id']
    record=json.loads((tmp_path/'fetched'/'record.json').read_text())
    assert record['attempt_id']==str(checkpoint['attempt'].id)
    assert (tmp_path/'fetched'/'READY').read_text()=='cs-catalog-v1\n'
    with pytest.raises(BackupError,match='new'):
        fetched(checkpoint,tmp_path)


def test_wrong_namespace_key_policy_and_record_id_fail_before_publication(checkpoint,tmp_path):
    publish_checkpoint(**checkpoint)
    for change in ({'namespace':str(uuid.uuid4())},{'expected_policy_fingerprint':'b'*64},
                   {'expected_schedule_id':uuid.uuid4()}):
        with pytest.raises((BackupError,ObjectStoreError)):
            fetched(checkpoint,tmp_path,**change)
        assert not (tmp_path/'fetched').exists()
    wrong_key=tmp_path/'wrong-key'; wrong_key.write_bytes(os.urandom(32))
    with pytest.raises(BackupError,match='authentication'):
        fetched(checkpoint,tmp_path,key_path=wrong_key)
    anchor=json.loads(checkpoint['anchor_path'].read_text()); anchor['record_id']=str(uuid.uuid4())
    checkpoint['anchor_path'].write_text(json.dumps(anchor))
    with pytest.raises(BackupError,match='authentication'):
        fetched(checkpoint,tmp_path)


def test_unknown_remote_write_ack_leaves_object_without_anchor_and_no_retry(checkpoint):
    checkpoint['store'].fail=True
    with pytest.raises(ObjectStoreError):
        publish_checkpoint(**checkpoint)
    assert checkpoint['store'].data and not checkpoint['anchor_path'].exists()


def test_source_receipt_mismatch_is_rejected_before_catalog_upload(checkpoint):
    checkpoint['report']['backup_id']=str(uuid.uuid4())
    with pytest.raises(BackupError,match='recovery point'):
        publish_checkpoint(**checkpoint)
    assert not checkpoint['store'].data and not checkpoint['anchor_path'].exists()


def test_lost_guard_after_upload_keeps_version_without_anchor(checkpoint):
    calls=[]
    def guard():
        calls.append(True)
        if len(calls)==3:
            raise RuntimeError('Synthetic lease lost')
    checkpoint['guard']=guard
    with pytest.raises(RuntimeError,match='lease lost'):
        publish_checkpoint(**checkpoint)
    assert checkpoint['store'].data and not checkpoint['anchor_path'].exists()


def test_catalog_rejects_unknown_sensitive_report_fields_before_remote_write(checkpoint):
    checkpoint['report']['secret_key']='synthetic-sensitive-only'
    with pytest.raises(BackupError,match='evidence'):
        publish_checkpoint(**checkpoint)
    assert not checkpoint['store'].data


def test_fetch_cli_requires_isolation_and_explicit_credentials_before_client(tmp_path,monkeypatch,capsys):
    import scripts.backup_catalog as cli
    monkeypatch.setattr(cli,'operator_store',lambda **k:pytest.fail('Unauthorized credentials requested'))
    monkeypatch.setattr(sys,'argv',['backup_catalog.py','--anchor',str(tmp_path/'anchor.json'),
        '--destination',str(tmp_path/'checkpoint'),'--namespace',str(uuid.uuid4()),
        '--schedule-id',str(uuid.uuid4()),'--policy-fingerprint','a'*64,'--key-path',str(tmp_path/'key'),
        '--scratch-root',str(tmp_path),'--storage-policy',str(tmp_path/'policy.json'),'--storage-target','catalog'])
    assert cli.main()==1 and not capsys.readouterr().out
