"""Encrypted immutable catalog checkpoints and fixed-version disaster bootstrap.

An independent offline anchor/key/policy is required; no latest discovery,
catalog replay, secret recovery, automatic deletion or service activation.
"""
import json
import hashlib
import math
import os
from pathlib import Path
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.backup import BackupError,_key,_workspace,_decrypt,checked_directory
from app.backup_archive import _reference,MAX_INDEX

MAGIC=b'CSCAT1\x00'
MAX_RECORD=65536


def _json(value):
    encoded=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(encoded)>MAX_RECORD:
        raise BackupError('Catalog checkpoint budget exceeded')
    return encoded


def _validate(record,namespace,record_id):
    if (not isinstance(record,dict) or set(record) != {'format','record_id','namespace','schedule_id',
            'policy_fingerprint','attempt_id','owner','fence','report','receipt'}
            or record['format'] != 1 or record['record_id'] != record_id or record['namespace'] != namespace):
        raise BackupError('Catalog checkpoint binding invalid')
    import re
    from datetime import datetime
    for field in ('schedule_id','attempt_id','owner'):
        if str(uuid.UUID(record[field])) != record[field]:
            raise BackupError('Invalid catalog identity')
    if (type(record['fence']) is not int or record['fence']<1
            or not isinstance(record['policy_fingerprint'],str)
            or re.fullmatch('[0-9a-f]{64}',record['policy_fingerprint']) is None):
        raise BackupError('Invalid catalog policy')
    report=record['report']; receipt=record['receipt']
    allowed={'backup_id','schema','files','objects','snapshot_at','elapsed_seconds','local_verified',
        'archive_id','remote_verified','chunks','restore_verified','restore_elapsed_seconds','restore_bucket'}
    required={'backup_id','schema','files','objects','snapshot_at','elapsed_seconds','local_verified','archive_id','remote_verified','chunks'}
    if (not isinstance(report,dict) or report.get('local_verified') is not True or report.get('remote_verified') is not True
            or set(report)-allowed or required-set(report)
            or not isinstance(receipt,dict) or set(receipt) != {'format','archive_id','namespace','index'}
            or receipt['format'] != 1 or receipt['namespace'] != namespace
            or receipt['archive_id'] != report.get('archive_id')):
        raise BackupError('Catalog archive evidence invalid')
    uuid.UUID(report['backup_id']); uuid.UUID(receipt['archive_id'])
    if not isinstance(report['schema'],str) or not report['schema']:
        raise BackupError('Catalog schema required')
    for name in ('files','objects','chunks'):
        if type(report[name]) is not int or report[name] < (1 if name=='chunks' else 0):
            raise BackupError('Invalid catalog count')
    for name in ('elapsed_seconds','restore_elapsed_seconds'):
        if name in report and (type(report[name]) not in (int,float) or not math.isfinite(report[name]) or report[name]<0):
            raise BackupError('Invalid catalog duration')
    if 'restore_verified' in report and report['restore_verified'] is not True:
        raise BackupError('Invalid catalog restore evidence')
    if 'restore_verified' in report and 'restore_elapsed_seconds' not in report:
        raise BackupError('Incomplete catalog restore evidence')
    if ('restore_elapsed_seconds' in report or 'restore_bucket' in report) and report.get('restore_verified') is not True:
        raise BackupError('Unbound catalog restore evidence')
    if datetime.fromisoformat(report['snapshot_at']).utcoffset() is None:
        raise BackupError('Catalog snapshot timezone required')
    _reference(receipt['index'],namespace)


def _decode(wire,key,namespace,record_id):
    if len(wire)>MAX_RECORD+64 or not wire.startswith(MAGIC):
        raise BackupError('Invalid bounded catalog ciphertext')
    try:
        nonce=wire[len(MAGIC):len(MAGIC)+12]
        plain=AESGCM(key).decrypt(nonce,wire[len(MAGIC)+12:],('cs-catalog-v1:'+namespace+':'+record_id).encode())
        record=json.loads(plain)
        _validate(record,namespace,record_id)
        return record
    except Exception:
        raise BackupError('Catalog checkpoint authentication or binding failed') from None


def publish_checkpoint(*,store,archive_store,namespace,plan,attempt,report,receipt_path,anchor_path,key_path,scratch_root,guard):
    namespace=str(uuid.UUID(namespace))
    receipt_path=checked_directory(receipt_path)
    anchor_path=checked_directory(anchor_path)
    scratch=checked_directory(scratch_root,existing=True)
    if (receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size>4096
            or anchor_path.exists() or anchor_path.is_symlink() or not anchor_path.parent.is_dir()):
        raise BackupError('Explicit bounded receipt and new catalog anchor required')
    guard()
    key=_key(key_path)
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    record_id=str(uuid.uuid4())
    record={'format':1,'record_id':record_id,'namespace':namespace,'schedule_id':str(plan.schedule_id),
        'policy_fingerprint':plan.fingerprint,'attempt_id':str(attempt.id),'owner':str(attempt.owner),
        'fence':attempt.fence,'report':report,'receipt':receipt}
    _validate(record,namespace,record_id)
    ref=_reference(receipt['index'],namespace)
    if ref.size>MAX_INDEX+64:
        raise BackupError('Catalog archive index exceeds budget')
    # Authenticate receipt against its exact encrypted remote index before publishing.
    with _workspace(scratch) as temp:
        encrypted,plain=temp/'index.enc',temp/'index.json'
        encrypted.write_bytes(archive_store.get(namespace,ref))
        _decrypt(encrypted,plain,key,'remote-index:'+receipt['archive_id'])
        index=json.loads(plain.read_text(encoding='utf-8'))
        if any(index.get(name)!=value for name,value in {'format':1,'namespace':namespace,
            'archive_id':receipt['archive_id'],'backup_id':report['backup_id'],'snapshot_at':report['snapshot_at']}.items()):
            raise BackupError('Catalog receipt does not match archive recovery point')
    guard()
    plain=_json(record); nonce=os.urandom(12)
    wire=MAGIC+nonce+AESGCM(key).encrypt(nonce,plain,('cs-catalog-v1:'+namespace+':'+record_id).encode())
    reference=store.put(namespace,wire,kind='backup')
    guard()
    if _decode(store.get(namespace,reference),key,namespace,record_id)!=record:
        raise BackupError('Catalog checkpoint readback mismatch')
    anchor={'format':1,'namespace':namespace,'record_id':record_id,'record':reference.to_dict()}
    with _workspace(anchor_path.parent) as temp:
        pointer=temp/'anchor.json'
        with pointer.open('xb') as target:
            target.write(_json(anchor)); target.flush(); os.fsync(target.fileno())
        guard()
        os.link(pointer,anchor_path)  # Exclusive publication; never replace unknown state.
    return {'catalog_verified':True,'catalog_record_id':record_id,'catalog_reference':reference.to_dict()}


def read_checkpoint(*,store,namespace,anchor_path,key_path,expected_schedule_id,
                    expected_policy_fingerprint,expected_anchor_sha256=None):
    namespace=str(uuid.UUID(namespace))
    anchor=checked_directory(anchor_path)
    if anchor_path.is_symlink() or not anchor.is_file():
        raise BackupError('Explicit bounded catalog anchor required')
    with anchor.open('rb') as source:
        raw=source.read(4097)
    if len(raw)>4096:
        raise BackupError('Catalog anchor exceeds budget')
    digest=hashlib.sha256(raw).hexdigest()
    if expected_anchor_sha256 is not None and digest!=expected_anchor_sha256:
        raise BackupError('Catalog anchor changed after selection')
    value=json.loads(raw)
    if set(value)!={'format','namespace','record_id','record'} or value['format']!=1 or value['namespace']!=namespace:
        raise BackupError('Catalog anchor namespace invalid')
    record_id=str(uuid.UUID(value['record_id']))
    reference=_reference(value['record'],namespace)
    if reference.size>MAX_RECORD+64:
        raise BackupError('Catalog checkpoint exceeds budget')
    record=_decode(store.get(namespace,reference),_key(key_path),namespace,record_id)
    if record['schedule_id']!=str(uuid.UUID(str(expected_schedule_id))) or record['policy_fingerprint']!=expected_policy_fingerprint:
        raise BackupError('Catalog checkpoint differs from approved recovery policy')
    return record,digest


def fetch_checkpoint(*,store,namespace,anchor_path,destination,key_path,scratch_root,
                     expected_schedule_id,expected_policy_fingerprint,expected_anchor_sha256=None):
    output=checked_directory(destination)
    checked_directory(scratch_root,existing=True)
    if output.exists() or not output.parent.is_dir():
        raise BackupError('Explicit new isolated destination required')
    record,_=read_checkpoint(store=store,namespace=namespace,anchor_path=anchor_path,key_path=key_path,
        expected_schedule_id=expected_schedule_id,expected_policy_fingerprint=expected_policy_fingerprint,
        expected_anchor_sha256=expected_anchor_sha256)
    record_id=record['record_id']
    # Plaintext is published only after full authentication. No source DB connection.
    with _workspace(output.parent) as temp:
        staging=temp/'checkpoint'; staging.mkdir(mode=0o700)
        for name,content in (('record.json',_json(record)),('receipt.json',_json(record['receipt'])),('READY',b'cs-catalog-v1\n')):
            with (staging/name).open('xb') as target:
                target.write(content); target.flush(); os.fsync(target.fileno())
        # Reserve destination exclusively. Unknown/partial publication is retained.
        output.mkdir(mode=0o700)
        for name in ('record.json','receipt.json','READY'):
            (staging/name).rename(output/name)
    return {'record_id':record_id,'backup_id':record['report']['backup_id'],
        'snapshot_at':record['report']['snapshot_at'],'verified':True,'workers_enabled':False}
