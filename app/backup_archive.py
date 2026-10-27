"""Authenticated versioned remote archives of encrypted logical backup packages.

All remote blobs use exact versions; no latest fallback, public links, implicit
retry, deletion or source credential recovery. Receipt publication is last.
"""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import uuid

from app.backup import (BackupError, MAX_MANIFEST, _key, _file, _hash, _decrypt,
                        _encrypt, _workspace, checked_directory)
from app.object_store import ObjectReference

CHUNK_BYTES = 4 * 1024 * 1024
MAX_CHUNKS = 8192
MAX_FILES = 10002
MAX_INDEX = 16 * 1024 * 1024


def verify_package(package, key, scratch):
    """Authenticate every logical blob before archiving/publishing a fetched package."""
    package = checked_directory(package,existing=True)
    with _workspace(scratch) as temp:
        marker = _file(package,'READY')
        if marker.stat().st_size > 32 or marker.read_text(encoding='ascii') != 'cs-backup-v1\n':
            raise BackupError('Incomplete logical backup package')
        manifest_blob = _file(package,'manifest.enc')
        if manifest_blob.stat().st_size > MAX_MANIFEST+64:
            raise BackupError('Backup manifest too large')
        manifest_wire = _hash(manifest_blob)
        plain = temp/'manifest.json'
        _decrypt(manifest_blob,plain,key,'manifest')
        manifest = json.loads(plain.read_text(encoding='utf-8'))
        if manifest['format'] not in {1,2} or len(manifest['files']) > MAX_FILES-2:
            raise BackupError('Invalid bounded backup manifest')
        uuid.UUID(manifest['backup_id'])
        blobs = [('database','database.enc',manifest['dump'])]
        for row in manifest['files']:
            if not isinstance(row['blob'],str) or not re.fullmatch(r'file-[0-9]+',row['blob']):
                raise BackupError('Invalid logical backup blob')
            blobs.append((row['blob'],row['blob']+'.enc',{'size':row['size'],'sha256':row['sha256']}))
        names = {'READY','manifest.enc',*(name for _,name,_ in blobs)}
        if len(names) != len(blobs)+2 or {p.name for p in package.iterdir()} != names:
            raise BackupError('Backup package inventory mismatch')
        if sum(_file(package,name).stat().st_size for name in names) > CHUNK_BYTES*MAX_CHUNKS:
            raise BackupError('Logical backup size budget exceeded')
        wire = {name:_hash(_file(package,name)) for name in names}
        if wire['manifest.enc'] != manifest_wire:
            raise BackupError('Backup manifest changed during verification')
        for label,name,expected in blobs:
            dest = temp/'verify.bin'
            _decrypt(_file(package,name),dest,key,label)
            if dict(zip(('size','sha256'),_hash(dest))) != expected:
                raise BackupError('Logical backup blob integrity mismatch')
            if _hash(_file(package,name)) != wire[name]:
                raise BackupError('Backup blob changed during verification')
            dest.unlink()
        if any(_hash(_file(package,name)) != wire[name] for name in names):
            raise BackupError('Backup package changed during verification')
        return manifest,sorted(names),wire


def archive_package(*, package:Path, receipt:Path, store, namespace:str,
                    key_path:Path, scratch_root:Path,guard=None):
    guard = guard or (lambda:None)
    guard()
    namespace = str(uuid.UUID(namespace))
    package = checked_directory(package,existing=True)
    receipt = checked_directory(receipt)
    scratch = checked_directory(scratch_root,existing=True)
    if receipt.exists() or package == receipt.parent or package in receipt.parents or not receipt.parent.is_dir():
        raise BackupError('Explicit new receipt outside backup package required')
    key = _key(key_path)
    manifest,names,wire = verify_package(package,key,scratch)
    if sum((wire[name][0]+CHUNK_BYTES-1)//CHUNK_BYTES for name in names) > MAX_CHUNKS:
        raise BackupError('Remote backup chunk budget exceeded')
    store.check_ready()
    archive_id = str(uuid.uuid4())
    index = {'format':1,'archive_id':archive_id,'namespace':namespace,'backup_id':manifest['backup_id'],
             'snapshot_at':manifest['snapshot_at'],'files':[]}
    chunks = 0
    for name in names:
        path = _file(package,name)
        before = wire[name]
        entry = {'name':name,'size':before[0],'sha256':before[1],'chunks':[]}
        digest,size = hashlib.sha256(),0
        with path.open('rb') as source:
            while data := source.read(CHUNK_BYTES):
                guard()
                chunks += 1
                if chunks > MAX_CHUNKS:
                    raise BackupError('Remote backup size budget exceeded')
                digest.update(data); size += len(data)
                entry['chunks'].append(store.put(namespace,data,kind='backup').to_dict())
                guard()
        if (size,digest.hexdigest()) != before or _hash(path) != before:
            raise BackupError('Backup package changed during archive; keep remote objects for reconciliation')
        index['files'].append(entry)
    if any(_hash(_file(package,name)) != wire[name] for name in names):
        raise BackupError('Backup package changed before index publication')
    with _workspace(scratch) as temp:
        plain,encrypted = temp/'index.json',temp/'index.enc'
        encoded = json.dumps(index,sort_keys=True,separators=(',',':')).encode()
        if len(encoded) > MAX_INDEX:
            raise BackupError('Remote backup index budget exceeded')
        plain.write_bytes(encoded)
        _encrypt(plain,encrypted,key,'remote-index:'+archive_id)
        reference = store.put(namespace,encrypted.read_bytes(),kind='backup')
        guard()
    # Unique receipt is never overwritten, and is published only after readback.
    value = {'format':1,'archive_id':archive_id,'namespace':namespace,'index':reference.to_dict()}
    # Same-volume hard link atomically publishes the complete receipt and fails
    # if another writer already reserved this name. Never replace or truncate it.
    with _workspace(receipt.parent) as temp:
        pointer = temp/'receipt.json'
        with pointer.open('x',encoding='utf-8') as target:
            json.dump(value,target,sort_keys=True)
            target.flush(); os.fsync(target.fileno())
        guard()
        os.link(pointer,receipt)
    return {'archive_id':archive_id,'backup_id':manifest['backup_id'],'snapshot_at':manifest['snapshot_at'],
            'files':len(names),'chunks':chunks,'remote_verified':True}


def _reference(value,namespace):
    reference = ObjectReference.from_dict(value)
    if not reference.key.startswith(f'tenants/{namespace}/backup/'):
        raise BackupError('Remote backup reference outside archive namespace')
    return reference


def fetch_package(*, receipt:Path, destination:Path, store, namespace:str,
                  key_path:Path, scratch_root:Path):
    namespace = str(uuid.UUID(namespace))
    receipt_path = checked_directory(receipt)
    if receipt.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size > 4096:
        raise BackupError('Explicit bounded archive receipt required')
    value = json.loads(receipt_path.read_text(encoding='utf-8'))
    archive_id = str(uuid.UUID(value['archive_id']))
    if value['format'] != 1 or value['namespace'] != namespace:
        raise BackupError('Archive receipt namespace mismatch')
    reference = _reference(value['index'],namespace)
    if reference.size > MAX_INDEX+64:
        raise BackupError('Remote backup index too large')
    output = checked_directory(destination)
    scratch = checked_directory(scratch_root,existing=True)
    if output.exists() or not output.parent.is_dir() or output == receipt_path.parent or output in receipt_path.parents:
        raise BackupError('Fetched package requires new destination')
    key = _key(key_path)
    published = False
    with _workspace(scratch) as temp:
        encrypted,plain = temp/'index.enc',temp/'index.json'
        encrypted.write_bytes(store.get(namespace,reference))
        _decrypt(encrypted,plain,key,'remote-index:'+archive_id)
        index = json.loads(plain.read_text(encoding='utf-8'))
        if (index['format'] != 1 or index['archive_id'] != archive_id or index['namespace'] != namespace
                or not isinstance(index['files'],list) or not 3 <= len(index['files']) <= MAX_FILES+1):
            raise BackupError('Invalid remote backup index')
        seen,refs,chunks,total = set(),set(),0,0
        for entry in index['files']:
            name = entry['name']
            if (not isinstance(name,str) or not re.fullmatch(r'(READY|database\.enc|manifest\.enc|file-[0-9]+\.enc)',name)
                    or name in seen or type(entry['size']) is not int or entry['size'] < 0
                    or not re.fullmatch('[0-9a-f]{64}',entry['sha256']) or not isinstance(entry['chunks'],list)):
                raise BackupError('Invalid remote archive inventory')
            seen.add(name)
            sizes = 0
            for obj in entry['chunks']:
                ref = _reference(obj,namespace)
                store._validate(namespace,ref)
                identity = (ref.bucket,ref.key,ref.version_id)
                if identity in refs or type(ref.size) is not int or not 0 < ref.size <= CHUNK_BYTES:
                    raise BackupError('Invalid remote chunk inventory')
                refs.add(identity); chunks += 1; sizes += ref.size
            if sizes != entry['size']:
                raise BackupError('Remote chunk size mismatch')
            total += sizes
        if not {'READY','database.enc','manifest.enc'} <= seen or chunks > MAX_CHUNKS or total > CHUNK_BYTES*MAX_CHUNKS:
            raise BackupError('Remote backup inventory budget exceeded')
        staging = temp/'package'; staging.mkdir()
        for entry in index['files']:
            digest,size = hashlib.sha256(),0
            with (staging/entry['name']).open('xb') as target:
                for obj in entry['chunks']:
                    data = store.get(namespace,_reference(obj,namespace))
                    target.write(data); digest.update(data); size += len(data)
            if (size,digest.hexdigest()) != (entry['size'],entry['sha256']):
                raise BackupError('Fetched ciphertext integrity mismatch')
        manifest,names,_ = verify_package(staging,key,scratch)
        if manifest['backup_id'] != index['backup_id'] or manifest['snapshot_at'] != index['snapshot_at']:
            raise BackupError('Fetched logical backup identity mismatch')
        try:
            output.mkdir(mode=0o700)  # Exclusive: never replace a prior package.
            published = True
            for name in names:
                if name != 'READY':
                    (staging/name).rename(output/name)
            (staging/'READY').rename(output/'READY')
        except Exception:
            if published:
                shutil.rmtree(output)
            raise
    return {'archive_id':archive_id,'backup_id':manifest['backup_id'],'snapshot_at':manifest['snapshot_at'],
            'files':len(names),'chunks':chunks,'verified':True,'workers_enabled':False}
