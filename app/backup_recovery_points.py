"""Strict recovery-point selection within an independently approved anchor set."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from app.backup import BackupError,checked_directory
from app.backup_catalog_archive import read_checkpoint


def _time(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise BackupError('Explicit timezone-aware recovery time required')
    return value.astimezone(timezone.utc)


def select_recovery_point(*,store,namespace,manifest_path,expected_manifest_sha256,key_path,
                         expected_schedule_id,expected_policy_fingerprint,not_after,max_gap_seconds,
                         min_snapshot=None,require_restore=True):
    cutoff=_time(not_after)
    minimum=_time(min_snapshot) if min_snapshot is not None else None
    now=datetime.now(timezone.utc)
    if (cutoff>now or (minimum is not None and minimum>cutoff)
            or type(max_gap_seconds) is not int or not 60<=max_gap_seconds<=2592000
            or type(require_restore) is not bool or not isinstance(expected_manifest_sha256,str)
            or re.fullmatch('[0-9a-f]{64}',expected_manifest_sha256) is None):
        raise BackupError('Invalid approved recovery selection policy')
    namespace=str(uuid.UUID(namespace))
    path=checked_directory(manifest_path)
    if manifest_path.is_symlink() or not path.is_file():
        raise BackupError('Explicit independent anchor manifest required')
    with path.open('rb') as source:
        raw=source.read(65537)
    if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=expected_manifest_sha256:
        raise BackupError('Anchor manifest budget or independent digest mismatch')
    manifest=json.loads(raw)
    if (not isinstance(manifest,dict) or set(manifest)!={'format','anchors'} or type(manifest['format']) is not int
            or manifest['format']!=1 or not isinstance(manifest['anchors'],list) or not 1<=len(manifest['anchors'])<=100):
        raise BackupError('Bounded approved anchor set required')
    candidates=[]; records=set(); attempts=set(); paths=set(); eligible=[]
    # Fail entire selection if any listed record is unavailable/invalid: no hidden fallback.
    for entry in manifest['anchors']:
        if (not isinstance(entry,dict) or set(entry)!={'path','sha256'} or not isinstance(entry['path'],str)
                or not isinstance(entry['sha256'],str) or re.fullmatch('[0-9a-f]{64}',entry['sha256']) is None):
            raise BackupError('Explicit absolute anchor paths and independent digests required')
        anchor=Path(entry['path']); identity=str(anchor.resolve())
        if identity in paths:
            raise BackupError('Duplicate anchor path in approved set')
        paths.add(identity)
        record,digest=read_checkpoint(store=store,namespace=namespace,anchor_path=anchor,key_path=key_path,
            expected_schedule_id=expected_schedule_id,expected_policy_fingerprint=expected_policy_fingerprint,
            expected_anchor_sha256=entry['sha256'])
        if record['record_id'] in records or record['attempt_id'] in attempts:
            raise BackupError('Duplicate or branched checkpoint attempt requires review')
        records.add(record['record_id']); attempts.add(record['attempt_id'])
        snapshot=_time(datetime.fromisoformat(record['report']['snapshot_at']))
        if snapshot>now:
            raise BackupError('Future checkpoint recovery point requires review')
        lag=(cutoff-snapshot).total_seconds()
        accepted=(0<=lag<=max_gap_seconds and (minimum is None or snapshot>=minimum)
            and (not require_restore or record['report'].get('restore_verified') is True))
        candidate={'record_id':record['record_id'],'backup_id':record['report']['backup_id'],
            'snapshot_at':snapshot.isoformat(),'anchor_path':str(anchor),'anchor_sha256':digest,
            'restore_verified':record['report'].get('restore_verified') is True,'eligible':accepted}
        candidates.append(candidate)
        if accepted:
            eligible.append((snapshot,candidate))
    if not eligible:
        raise BackupError('No approved recovery point satisfies verification/time constraints')
    eligible.sort(key=lambda item:item[0],reverse=True)
    if len(eligible)>1 and eligible[0][0]==eligible[1][0]:
        raise BackupError('Ambiguous newest recovery point requires explicit review')
    chosen=eligible[0][1]
    return {'records_authenticated':True,'complete_catalog_proven':False,'workers_enabled':False,
        'manifest_sha256':expected_manifest_sha256,'requested_not_after':cutoff.isoformat(),
        'max_gap_seconds':max_gap_seconds,'require_restore':require_restore,
        'selected':chosen,'candidates':candidates}
