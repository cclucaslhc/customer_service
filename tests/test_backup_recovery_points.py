"""Authenticated anchor-set selection with synthetic encrypted records only."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import sys
import uuid

import pytest
from app.backup import BackupError,_encrypt
from app.backup_catalog_archive import publish_checkpoint,fetch_checkpoint
from app.backup_recovery_points import select_recovery_point
from app.backup_scheduler import BackupAttempt
from test_backup_catalog_archive import checkpoint


def point(options,tmp_path,name,snapshot,*,restored=True,attempt=None):
    report={**options['report'],'snapshot_at':snapshot.isoformat(),'backup_id':str(uuid.uuid4()),'archive_id':str(uuid.uuid4())}
    if restored:
        report.update(restore_verified=True,restore_elapsed_seconds=1)
    plain,encrypted=tmp_path/(name+'.index.json'),tmp_path/(name+'.index.enc')
    plain.write_text(json.dumps({'format':1,'namespace':options['namespace'],'archive_id':report['archive_id'],
        'backup_id':report['backup_id'],'snapshot_at':report['snapshot_at']}))
    _encrypt(plain,encrypted,options['key_path'].read_bytes(),'remote-index:'+report['archive_id'])
    ref=options['archive_store'].put(options['namespace'],encrypted.read_bytes(),kind='backup')
    receipt=tmp_path/(name+'.receipt.json')
    receipt.write_text(json.dumps({'format':1,'namespace':options['namespace'],'archive_id':report['archive_id'],'index':ref.to_dict()}))
    anchor=tmp_path/(name+'.anchor.json')
    publish_checkpoint(**{**options,'report':report,'receipt_path':receipt,'anchor_path':anchor,
        'attempt':attempt or BackupAttempt(uuid.uuid4(),uuid.uuid4(),1)})
    return anchor


def select(options,tmp_path,anchors,cutoff,**changes):
    manifest=tmp_path/'inventory.json'
    manifest.write_text(json.dumps({'format':1,'anchors':[{'path':str(path),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in anchors]}))
    return select_recovery_point(**{'store':options['store'],'namespace':options['namespace'],
        'manifest_path':manifest,'expected_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'key_path':options['key_path'],'expected_schedule_id':options['plan'].schedule_id,
        'expected_policy_fingerprint':options['plan'].fingerprint,'not_after':cutoff,'max_gap_seconds':3600,**changes})


def test_selects_newest_verified_recovery_point_not_newest_unrestored_record(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    older=point(checkpoint,tmp_path,'older',cutoff-timedelta(minutes=20))
    newest=point(checkpoint,tmp_path,'newest',cutoff-timedelta(minutes=1),restored=False)
    verified=point(checkpoint,tmp_path,'verified',cutoff-timedelta(minutes=10))
    result=select(checkpoint,tmp_path,[newest,older,verified],cutoff)
    assert result['selected']['anchor_path']==str(verified)
    assert not result['complete_catalog_proven'] and not result['workers_enabled']
    unverified=select(checkpoint,tmp_path,[older,newest,verified],cutoff,require_restore=False)
    assert unverified['selected']['anchor_path']==str(newest)


def test_future_cutoff_after_target_and_stale_or_below_minimum_points_rejected(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    old=point(checkpoint,tmp_path,'old',cutoff-timedelta(hours=2))
    after=point(checkpoint,tmp_path,'after',cutoff+timedelta(minutes=1))
    with pytest.raises(BackupError,match='No approved'):
        select(checkpoint,tmp_path,[old,after],cutoff)
    recent=point(checkpoint,tmp_path,'recent',cutoff-timedelta(minutes=10))
    with pytest.raises(BackupError,match='No approved'):
        select(checkpoint,tmp_path,[recent],cutoff,min_snapshot=cutoff-timedelta(minutes=5))
    with pytest.raises(BackupError,match='policy'):
        select(checkpoint,tmp_path,[recent],datetime.now(timezone.utc)+timedelta(hours=1))


def test_any_unreadable_record_invalidates_selection_instead_of_silent_fallback(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    older=point(checkpoint,tmp_path,'older',cutoff-timedelta(minutes=20))
    newest=point(checkpoint,tmp_path,'newest',cutoff-timedelta(minutes=10))
    anchor=json.loads(newest.read_text())
    del checkpoint['store'].data[anchor['record']['key']]
    with pytest.raises(KeyError):
        select(checkpoint,tmp_path,[older,newest],cutoff)


def test_duplicate_paths_attempt_branches_and_tied_points_require_review(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    attempt=BackupAttempt(uuid.uuid4(),uuid.uuid4(),1)
    first=point(checkpoint,tmp_path,'first',cutoff-timedelta(minutes=20),attempt=attempt)
    with pytest.raises(BackupError,match='Duplicate anchor'):
        select(checkpoint,tmp_path,[first,first],cutoff)
    branch=point(checkpoint,tmp_path,'branch',cutoff-timedelta(minutes=10),attempt=attempt)
    with pytest.raises(BackupError,match='branched'):
        select(checkpoint,tmp_path,[first,branch],cutoff)
    tied=point(checkpoint,tmp_path,'tied',cutoff-timedelta(minutes=20))
    with pytest.raises(BackupError,match='Ambiguous'):
        select(checkpoint,tmp_path,[first,tied],cutoff)


def test_independent_inventory_digest_and_selected_anchor_prevent_replacement(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    anchor=point(checkpoint,tmp_path,'candidate',cutoff-timedelta(minutes=10))
    with pytest.raises(BackupError,match='digest'):
        select(checkpoint,tmp_path,[anchor],cutoff,expected_manifest_sha256='b'*64)
    selection=select(checkpoint,tmp_path,[anchor],cutoff)
    anchor.write_text(anchor.read_text()+' ')
    with pytest.raises(BackupError,match='changed after selection'):
        select_recovery_point(store=checkpoint['store'],namespace=checkpoint['namespace'],manifest_path=tmp_path/'inventory.json',
            expected_manifest_sha256=selection['manifest_sha256'],key_path=checkpoint['key_path'],
            expected_schedule_id=checkpoint['plan'].schedule_id,expected_policy_fingerprint=checkpoint['plan'].fingerprint,
            not_after=cutoff,max_gap_seconds=3600)
    with pytest.raises(BackupError,match='changed after selection'):
        fetch_checkpoint(store=checkpoint['store'],namespace=checkpoint['namespace'],anchor_path=anchor,
            expected_anchor_sha256=selection['selected']['anchor_sha256'],destination=tmp_path/'fetched',
            key_path=checkpoint['key_path'],scratch_root=tmp_path,expected_schedule_id=checkpoint['plan'].schedule_id,
            expected_policy_fingerprint=checkpoint['plan'].fingerprint)
    assert not (tmp_path/'fetched').exists()


def test_missing_restore_evidence_does_not_get_default_waiver(checkpoint,tmp_path):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    anchor=point(checkpoint,tmp_path,'candidate',cutoff-timedelta(minutes=10),restored=False)
    with pytest.raises(BackupError,match='No approved'):
        select(checkpoint,tmp_path,[anchor],cutoff)
    assert select(checkpoint,tmp_path,[anchor],cutoff,require_restore=False)['selected']['eligible']


def test_cli_denies_credential_read_without_isolated_review(tmp_path,monkeypatch,capsys):
    import scripts.backup_recovery_points as cli
    monkeypatch.setattr(cli,'operator_store',lambda **k:pytest.fail('Missing review requested credentials'))
    monkeypatch.setattr(sys,'argv',['backup_recovery_points.py','--manifest',str(tmp_path/'inventory.json'),
        '--manifest-sha256','a'*64,'--namespace',str(uuid.uuid4()),'--schedule-id',str(uuid.uuid4()),
        '--policy-fingerprint','a'*64,'--not-after','2026-10-07T00:00:00+00:00','--max-gap-seconds','3600',
        '--key-path',str(tmp_path/'key'),'--scratch-root',str(tmp_path),'--storage-policy',str(tmp_path/'policy.json'),
        '--storage-target','catalog'])
    assert cli.main()==1 and not capsys.readouterr().out


def test_cli_selects_and_fetches_hash_pinned_checkpoint_without_source_db(checkpoint,tmp_path,monkeypatch,capsys):
    import scripts.backup_recovery_points as cli
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    anchor=point(checkpoint,tmp_path,'candidate',cutoff-timedelta(minutes=10))
    expected=select(checkpoint,tmp_path,[anchor],cutoff)
    closed=[]; checkpoint['store'].close=lambda:closed.append(True)
    monkeypatch.setattr(cli,'operator_store',lambda **k:checkpoint['store'])
    monkeypatch.setattr(sys,'argv',['backup_recovery_points.py','--manifest',str(tmp_path/'inventory.json'),
        '--manifest-sha256',expected['manifest_sha256'],'--namespace',checkpoint['namespace'],
        '--schedule-id',str(checkpoint['plan'].schedule_id),'--policy-fingerprint',checkpoint['plan'].fingerprint,
        '--not-after',cutoff.isoformat(),'--max-gap-seconds','3600','--key-path',str(checkpoint['key_path']),
        '--scratch-root',str(tmp_path),'--storage-policy',str(tmp_path/'policy.json'),'--storage-target','catalog',
        '--storage-credentials-stdin','--confirm-isolated-recovery-selection','--destination',str(tmp_path/'selected')])
    assert cli.main()==0 and closed==[True]
    output=json.loads(capsys.readouterr().out)
    assert output['fetch']['verified'] and not output['workers_enabled']
    assert (tmp_path/'selected'/'READY').exists()
