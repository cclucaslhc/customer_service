"""Explicit encrypted logical backup; no application bootstrap or secret discovery.

Offline maintenance is required: this is not WAL/PITR, an online file snapshot,
or permission-revocation reconciliation. Never enable restored Workers here.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.persistence.engine import build_engine
from app.persistence.schema import metadata, SCHEMA_REVISION


class BackupError(RuntimeError):
    """Safe to expose without database content or credentials."""


MAGIC = b'CSBK1\x00'
AAD = b'customer-service-logical-backup-v1:'
CHUNK = 1024 * 1024
MAX_MANIFEST = 16 * CHUNK


def checked_directory(path: Path, *, existing=False) -> Path:
    if not path.is_absolute():
        raise BackupError('Absolute explicit paths required')
    resolved = path.resolve()
    project_data = Path(__file__).resolve().parents[1] / 'data'
    if resolved == Path(resolved.anchor) or resolved == project_data or project_data in resolved.parents:
        raise BackupError('Unsafe backup path')
    if any(part.lower() == 'secrets' or part.lower().startswith('.env') for part in resolved.parts):
        raise BackupError('Secret paths are not backup inputs')
    if existing and (not resolved.is_dir() or path.is_symlink()):
        raise BackupError('Explicit existing directory required')
    return resolved


def _key(path: Path) -> bytes:
    checked_directory(path.parent, existing=True)
    if path.is_symlink() or path.name.lower().startswith('.env'):
        raise BackupError('Invalid backup key path')
    with path.open('rb') as source:
        result = source.read(33)
    if len(result) != 32:
        raise BackupError('Backup key must contain exactly 32 random bytes')
    return result


def _relative(name: str) -> str:
    value = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or value.is_absolute()
            or any(part in {'..', '.', ''} for part in name.split('/'))):
        raise BackupError('Invalid manifest path')
    return name


def _file(root: Path, name: str) -> Path:
    path = root.joinpath(*PurePosixPath(_relative(name)).parts)
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise BackupError('Symbolic links are not backup inputs')
        cursor = cursor.parent
    if root not in path.resolve().parents:
        raise BackupError('Manifest path outside root')
    return path


def _hash(path: Path) -> tuple[int, str]:
    count, digest = 0, hashlib.sha256()
    with path.open('rb') as source:
        while data := source.read(CHUNK):
            count += len(data)
            digest.update(data)
    return count, digest.hexdigest()


def _encrypt(source: Path, target: Path, key: bytes, label: str):
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(AAD + label.encode())
    digest, size = hashlib.sha256(), 0
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        outgoing.write(MAGIC + nonce)
        while data := incoming.read(CHUNK):
            digest.update(data)
            size += len(data)
            outgoing.write(encryptor.update(data))
        outgoing.write(encryptor.finalize())
        outgoing.write(encryptor.tag)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    return {'size': size, 'sha256': digest.hexdigest()}


def _decrypt(source: Path, target: Path, key: bytes, label: str):
    length = source.stat().st_size
    if length < len(MAGIC) + 12 + 16:
        raise BackupError('Invalid encrypted backup')
    with source.open('rb') as incoming:
        if incoming.read(len(MAGIC)) != MAGIC:
            raise BackupError('Unknown backup format')
        nonce = incoming.read(12)
        incoming.seek(-16, 2)
        tag = incoming.read(16)
        incoming.seek(len(MAGIC) + 12)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(AAD + label.encode())
        remaining = length - len(MAGIC) - 12 - 16
        try:
            with target.open('xb') as outgoing:
                while remaining:
                    data = incoming.read(min(CHUNK, remaining))
                    if not data:
                        raise BackupError('Truncated backup')
                    remaining -= len(data)
                    outgoing.write(decryptor.update(data))
                outgoing.write(decryptor.finalize())
        except Exception:
            target.unlink(missing_ok=True)
            raise BackupError('Backup authentication failed') from None


def _canonical(value):
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, (datetime, date, Decimal, uuid.UUID)):
        return str(value)
    raise TypeError('Unsupported backup value')


def invalidate_restored_release_approvals(db):
    """Require fresh approval after restore; retain the old forensic record."""
    releases = metadata.tables['rag_release_intents']
    return db.execute(releases.update().where(releases.c.state.in_(('pending','approved')))
        .values(state='recovery_review',version=releases.c.version+1)).rowcount


def invalidate_restored_rollouts(db):
    """Never resume a restored traffic allocation without explicit recovery review."""
    rollouts = metadata.tables['rag_rollouts']
    return db.execute(rollouts.update().where(rollouts.c.state.in_(('staged','running','paused')))
        .values(state='recovery_review',basis_points=0,version=rollouts.c.version+1)).rowcount


def database_manifest(db):
    """Hash each row internally; manifests contain no plaintext row content."""
    if set(sa.inspect(db).get_table_names(schema='public')) != set(metadata.tables) | {'alembic_version'}:
        raise BackupError('Database table inventory mismatch')
    tables = {}
    for table in metadata.sorted_tables:
        count, digest = 0, hashlib.sha256()
        rows = db.execute(sa.select(table).order_by(*table.primary_key.columns)
                          .execution_options(stream_results=True, yield_per=1000))
        for row in rows.mappings():
            value = json.dumps(dict(row), sort_keys=True, separators=(',', ':'), default=_canonical)
            digest.update(hashlib.sha256(value.encode()).digest())
            count += 1
        tables[table.name] = {'rows': count, 'sha256': digest.hexdigest()}
    sequences = [dict(row) for row in db.execute(sa.text(
        "SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")).mappings()]
    version = db.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one()
    return {'schema': version, 'tables': tables, 'sequences': sequences}


def _references(db, *, include_objects=False):
    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
    result = []
    for kind, table_name in (('knowledge', 'documents'), ('assets', 'knowledge_assets')):
        table = metadata.tables[table_name]
        for row in db.execute(sa.select(table.c.id, table.c.tenant_id, table.c.stored_name, table.c.size, table.c.sha256, table.c.metadata_json)).mappings():
            info = row['metadata_json'] or {}
            if isinstance(info, str):
                info = json.loads(info)
            remote = info.get('_private_object')
            if '_private_object' in info and not include_objects:
                raise BackupError('Object-backed backup is not configured; refusing local-cache fallback')
            name = str(row['stored_name']).replace('\\', '/')
            prefix = '' if str(row['tenant_id']) == LEGACY_TENANT_ID else str(row['tenant_id']) + '/'
            reference = {'kind': kind, 'path': _relative(prefix + name), 'size': row['size'], 'sha256': row['sha256']}
            if '_private_object' in info:
                from app.object_store import ObjectReference
                obj = ObjectReference.from_dict(remote)
                if (obj.size != row['size'] or obj.sha256 != row['sha256']
                        or not obj.key.startswith(f"tenants/{row['tenant_id']}/{kind}/")):
                    raise BackupError('Object reference disagrees with database manifest')
                reference.update(object=obj.to_dict(), table=table_name, row_id=str(row['id']), tenant_id=str(row['tenant_id']))
            result.append(reference)
    inputs = metadata.tables['knowledge_job_items']
    for row in db.execute(sa.select(inputs).where(sa.or_(inputs.c.input_object.is_not(None),
            sa.and_(inputs.c.status == 'pending',inputs.c.sha256.is_not(None))))).mappings():
        if row['input_object'] is None:
            relative = _relative(row['relative_path'].replace('\\','/'))
            result.append({'kind':'knowledge','path':f"{row['tenant_id']}/{relative}",
                           'size':row['size'],'sha256':row['sha256']})
            continue
        if not include_objects:
            raise BackupError('Object-backed backup is not configured; refusing local-cache fallback')
        from app.object_store import ObjectReference
        obj = ObjectReference.from_dict(row['input_object'])
        if (obj.size != row['size'] or obj.sha256 != row['sha256']
                or not obj.key.startswith(f"tenants/{row['tenant_id']}/knowledge/")):
            raise BackupError('Queued input reference disagrees with database manifest')
        result.append({'kind':'knowledge', 'path':f"{row['tenant_id']}/jobs/{row['job_id']}/{row['position']}",
            'size':row['size'], 'sha256':row['sha256'], 'object':obj.to_dict(), 'table':'knowledge_job_items',
            'row_id':str(row['job_id']), 'position':row['position'], 'tenant_id':str(row['tenant_id'])})
    unique = {}
    for row in result:
        identity = (row['kind'], row['path'])
        if identity in unique and unique[identity] != row:
            raise BackupError('Conflicting file references')
        unique[identity] = row
    return sorted(unique.values(), key=lambda row: (row['kind'], row['path']))


def _tool(binary_dir: Path, name: str) -> str:
    root = checked_directory(binary_dir, existing=True)
    path = root / (name + ('.exe' if os.name == 'nt' else ''))
    if not path.is_file() or path.is_symlink():
        raise BackupError('Explicit PostgreSQL binaries required')
    return str(path)


def _validated_url(url):
    parsed = make_url(url)
    if (parsed.drivername != 'postgresql+psycopg' or parsed.host not in {'localhost', '127.0.0.1', '::1'}
            or not parsed.database or parsed.query):
        raise BackupError('Explicit loopback PostgreSQL URL without query options required')
    return parsed


def _command(binary_dir, name, url, arguments):
    parsed = _validated_url(url)
    # Do not inherit historical passwords, service files, libpq options or passfiles.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    env.update(PGHOST=parsed.host, PGPORT=str(parsed.port or 5432), PGUSER=parsed.username or '',
               PGDATABASE=parsed.database, PGPASSWORD=parsed.password or '', PGCONNECT_TIMEOUT='10',
               PGSSLMODE='prefer', PGPASSFILE=os.devnull)
    result = subprocess.run([_tool(binary_dir, name), '--no-password', *arguments], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if result.returncode:
        raise BackupError('PostgreSQL backup command failed')


def _require_matching_client_major(db, binary_dir: Path) -> None:
    """Reject a dump/restore toolchain that may emit unsupported server SQL."""
    try:
        server_major = int(db.exec_driver_sql('SHOW server_version_num').scalar_one()) // 10000
        for name in ('pg_dump', 'pg_restore', 'psql'):
            result = subprocess.run([_tool(binary_dir, name), '--version'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5, check=False)
            match = re.search(rb'\(PostgreSQL\) (\d+)(?:\.\d+)?', result.stdout)
            if result.returncode or not match or int(match.group(1)) != server_major:
                raise BackupError('PostgreSQL client and server major versions must match')
    except BackupError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired, sa.exc.SQLAlchemyError):
        raise BackupError('PostgreSQL client version could not be verified') from None


@contextmanager
def _workspace(parent):
    # Owner-only on POSIX; Windows operator must preconfigure ACLs on the parent.
    with tempfile.TemporaryDirectory(prefix='cs-backup-', dir=parent) as directory:
        os.chmod(directory, 0o700)
        yield Path(directory)


def create_backup(*, url, binary_dir: Path, knowledge_root: Path, asset_root: Path,
                  destination: Path, key_path: Path, scratch_root: Path, maintenance_confirmed=False, object_store=None,maintenance_guard=None):
    if not maintenance_confirmed:
        raise BackupError('Explicit offline maintenance confirmation required')
    guard = maintenance_guard or (lambda:None)
    guard()
    _validated_url(url)
    started = time.monotonic()
    roots = {'knowledge': checked_directory(knowledge_root, existing=True),
             'assets': checked_directory(asset_root, existing=True)}
    output = checked_directory(destination)
    scratch = checked_directory(scratch_root, existing=True)
    key = _key(key_path)
    if output.exists() or output.parent == key_path.parent.resolve() or any(
            output == root or root in output.parents or output in root.parents for root in roots.values()):
        raise BackupError('New backup directory separated from input and key required')
    engine = build_engine(url, pool_size=1)
    try:
        with _workspace(scratch) as temp, engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
            with db.begin():
                _require_matching_client_major(db, binary_dir)
                db.execute(sa.text('SET TRANSACTION READ ONLY'))
                snapshot = db.execute(sa.text('SELECT pg_export_snapshot()')).scalar_one()
                references = _references(db, include_objects=object_store is not None)
                manifest = {'format': 2 if any('object' in r for r in references) else 1, 'database': database_manifest(db), 'files': [],
                            'snapshot_at': db.execute(sa.text('SELECT transaction_timestamp()')).scalar_one().isoformat(),
                            'backup_id': str(uuid.uuid4())}
                if manifest['database']['schema'] != SCHEMA_REVISION:
                    raise BackupError('Backup schema revision mismatch')
                output.mkdir(mode=0o700)
                try:
                    dump = temp / 'database.dump'
                    _command(binary_dir, 'pg_dump', url, ['--format=custom', '--no-owner', '--no-acl',
                        '--snapshot=' + snapshot, '--file=' + str(dump)])
                    guard()
                    manifest['dump'] = _encrypt(dump, output / 'database.enc', key, 'database')
                    for position, reference in enumerate(references):
                        guard()
                        if 'object' in reference:
                            from app.object_store import ObjectReference
                            source = temp / ('object-' + str(position))
                            source.write_bytes(object_store.get(reference['tenant_id'], ObjectReference.from_dict(reference['object'])))
                        else:
                            source = _file(roots[reference['kind']], reference['path'])
                        label = 'file-' + str(position)
                        info = _encrypt(source, output / (label + '.enc'), key, label)
                        if info != {'size': reference['size'], 'sha256': reference['sha256']}:
                            raise BackupError('Referenced file missing or changed')
                        manifest['files'].append({**reference, 'blob': label})
                        guard()
                    plain = temp / 'manifest.json'
                    plain.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
                    if plain.stat().st_size > MAX_MANIFEST:
                        raise BackupError('Backup manifest too large')
                    _encrypt(plain, output / 'manifest.enc', key, 'manifest')
                    guard()
                    # Completion marker is published last; readers reject partial packages.
                    (output / 'READY').write_text('cs-backup-v1\n', encoding='ascii')
                except Exception:
                    # Exact new directory owned by this operation, never an existing backup.
                    shutil.rmtree(output)
                    raise
        return {'backup_id': manifest['backup_id'], 'files': len(manifest['files']), 'schema': manifest['database']['schema'],
                'objects': sum('object' in row for row in manifest['files']),
                'snapshot_at': manifest['snapshot_at'], 'elapsed_seconds': round(time.monotonic() - started, 3)}
    finally:
        engine.dispose()


def restore_backup(*, url, binary_dir: Path, source: Path, knowledge_root: Path, asset_root: Path,
                   key_path: Path, scratch_root: Path, isolated_confirmed=False, object_store=None):
    if not isolated_confirmed:
        raise BackupError('Explicit isolated empty restore confirmation required')
    _validated_url(url)
    started = time.monotonic()
    package = checked_directory(source, existing=True)
    if not (package / 'READY').is_file():
        raise BackupError('Incomplete backup')
    roots = {'knowledge': checked_directory(knowledge_root), 'assets': checked_directory(asset_root)}
    if (any(root.exists() for root in roots.values()) or roots['knowledge'] == roots['assets']
            or roots['knowledge'] in roots['assets'].parents or roots['assets'] in roots['knowledge'].parents) or any(
            root in package.parents or package in root.parents for root in roots.values()):
        raise BackupError('Restore requires separate new file roots')
    key = _key(key_path)
    scratch = checked_directory(scratch_root, existing=True)
    engine = build_engine(url, pool_size=1)
    created = []
    sql_started = False
    try:
        with engine.connect() as db:
            _require_matching_client_major(db, binary_dir)
            inspector = sa.inspect(db)
            if (inspector.get_table_names(schema='public') or inspector.get_view_names(schema='public')
                    or inspector.get_sequence_names(schema='public')
                    or set(inspector.get_schema_names()) - {'public', 'information_schema', 'pg_catalog'}):
                raise BackupError('Restore database must be empty')
        with _workspace(scratch) as temp:
            if (package / 'manifest.enc').stat().st_size > MAX_MANIFEST + 64:
                raise BackupError('Backup manifest too large')
            plain = temp / 'manifest.json'
            _decrypt(_file(package, 'manifest.enc'), plain, key, 'manifest')
            manifest = json.loads(plain.read_text(encoding='utf-8'))
            if manifest['format'] not in {1, 2}:
                raise BackupError('Unknown backup format')
            remote_files = [row for row in manifest['files'] if 'object' in row]
            if remote_files:
                if manifest['format'] != 2 or object_store is None:
                    raise BackupError('Object archive requires explicit private restore store')
                if any(row['object']['bucket'] == object_store.bucket for row in remote_files):
                    raise BackupError('Restore object bucket must differ from source bucket')
                object_store.require_empty_restore_bucket()
            dump = temp / 'database.dump'
            _decrypt(_file(package, 'database.enc'), dump, key, 'database')
            if dict(zip(('size', 'sha256'), _hash(dump))) != manifest['dump']:
                raise BackupError('Database archive integrity mismatch')
            for root in roots.values():
                root.mkdir(mode=0o700)
                created.append(root)
            seen = set()
            staged_objects = []
            for reference in manifest['files']:
                identity = (reference['kind'], reference['path'])
                if identity in seen:
                    raise BackupError('Duplicate restore path')
                seen.add(identity)
                target = temp / ('object-' + str(len(staged_objects))) if 'object' in reference else _file(roots[reference['kind']], reference['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                _decrypt(_file(package, _relative(reference['blob']) + '.enc'), target, key, reference['blob'])
                if _hash(target) != (reference['size'], reference['sha256']):
                    raise BackupError('Restored file integrity mismatch')
                if 'object' in reference:
                    staged_objects.append((reference, target))
            # All encrypted blobs verified before remote writes or SQL. Uploads
            # use unique attempt keys; failures retain versions for reconciliation.
            replacements = []
            for reference, target in staged_objects:
                new = object_store.put(reference['tenant_id'], target.read_bytes(), kind=reference['kind'])
                replacements.append((reference, new.to_dict()))
            # Decrypt and verify every file before any SQL can be applied.
            sql = temp / 'restore.sql'
            _command(binary_dir, 'pg_restore', url, ['--no-owner', '--no-acl', '--file=' + str(sql), str(dump)])
            hold = temp / 'hold.sql'
            backup_id = uuid.UUID(manifest['backup_id'])
            hold.write_text("INSERT INTO public.recovery_state(id,blocked,backup_id,restored_at) "
                f"VALUES(1,true,'{backup_id}',clock_timestamp()) ON CONFLICT(id) DO UPDATE "
                "SET blocked=true,backup_id=EXCLUDED.backup_id,restored_at=EXCLUDED.restored_at;\n", encoding='ascii')
            sql_started = True
            # psql wraps BOTH files in one transaction: the restored schema/data
            # are never committed without the execution hold already present.
            _command(binary_dir, 'psql', url, ['--no-psqlrc', '--single-transaction', '--set=ON_ERROR_STOP=on',
                '--file=' + str(sql), '--file=' + str(hold)])
            with engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
                db.execute(sa.text('SET TRANSACTION READ ONLY'))
                actual = database_manifest(db)
                expected = manifest['database']
                comparable = lambda value: {**value, 'tables': {k: v for k, v in value['tables'].items() if k != 'recovery_state'}}
                state = metadata.tables['recovery_state']
                marker = db.execute(sa.select(state)).mappings().one()
                if (not marker['blocked'] or str(marker['backup_id']) != str(backup_id)
                        or comparable(actual) != comparable(expected) or _references(db, include_objects=True) != [
                        {k: v for k, v in row.items() if k != 'blob'} for row in manifest['files']]):
                    raise BackupError('Restored database verification failed; keep isolated')
            # Relocate provenance pointers only after the original snapshot is
            # verified. Pending jobs and Run identities are NOT resumed/rebased.
            d = metadata.tables['documents']
            relocated_paths = 0
            with engine.begin() as db:
                marker = db.execute(sa.select(metadata.tables['recovery_state']).with_for_update()).mappings().one()
                if not marker['blocked'] or str(marker['backup_id']) != str(backup_id):
                    raise BackupError('Recovery protection changed before object remapping')
                # Original maintenance data was just verified against the source.
                # Invalidate old ownership atomically with remapping, while the
                # independent recovery hold remains committed and enforced.
                maintenance = metadata.tables['maintenance_state']
                db.execute(maintenance.update().values(blocked=False,owner=None,lease_until=None))
                schedules = metadata.tables['backup_schedules']
                disabled_schedules = db.execute(schedules.update().values(enabled=False)).rowcount
                backup_jobs = metadata.tables['backup_jobs']
                reviewed_jobs = db.execute(backup_jobs.update().where(backup_jobs.c.state == 'running').values(
                    state='needs_review',lease_until=None,finished_at=sa.func.clock_timestamp())).rowcount
                # Restored approvals must never become executable merely by
                # clearing the independent recovery hold. Preserve evidence.
                invalidate_restored_release_approvals(db)
                invalidate_restored_rollouts(db)
                for reference, new in replacements:
                    table = metadata.tables[reference['table']]
                    is_input = reference['table'] == 'knowledge_job_items'
                    predicate = (sa.and_(table.c.job_id == uuid.UUID(reference['row_id']), table.c.position == reference['position'])
                        if is_input else table.c.id == uuid.UUID(reference['row_id']))
                    row = db.execute(sa.select(table).where(predicate,
                        table.c.tenant_id == uuid.UUID(reference['tenant_id'])).with_for_update()).mappings().one()
                    old_reference = row['input_object'] if is_input else row['metadata_json'].get('_private_object')
                    if old_reference != reference['object']:
                        raise BackupError('Restored object reference changed before remapping')
                    if is_input:
                        values = {'input_object':new}
                    else:
                        values = {'metadata_json':{**row['metadata_json'], '_private_object':new}}
                    db.execute(table.update().where(predicate).values(**values))
                for row in db.execute(sa.select(d.c.id, d.c.tenant_id, d.c.stored_name, d.c.metadata_json)).mappings().all():
                    if '_private_object' in (row['metadata_json'] or {}):
                        continue
                    from app.persistence.chat_tenant_v1 import LEGACY_TENANT_ID
                    prefix = '' if str(row['tenant_id']) == LEGACY_TENANT_ID else str(row['tenant_id']) + '/'
                    path = _file(roots['knowledge'], prefix + str(row['stored_name']).replace('\\', '/'))
                    db.execute(d.update().where(d.c.id == row['id']).values(source_path=str(path)))
                    relocated_paths += 1
                expected_references = [{k: v for k, v in row.items() if k != 'blob'} for row in manifest['files']]
                mapping = {(row['table'], row['row_id'], row.get('position')): new for row, new in replacements}
                for row in expected_references:
                    if 'object' in row:
                        row['object'] = mapping[(row['table'], row['row_id'], row.get('position'))]
                if _references(db, include_objects=True) != expected_references:
                    raise BackupError('Remapped object references failed verification')
                configs = metadata.tables['storage_configs']
                rebind_count = db.execute(configs.update().values(state='recovery_rebind')).rowcount
                if any(row['blocked'] or row['owner'] is not None or row['lease_until'] is not None
                       for row in db.execute(sa.select(maintenance)).mappings()):
                    raise BackupError('Restored maintenance ownership was not invalidated')
            return {'backup_id': manifest['backup_id'], 'files': len(seen), 'schema': manifest['database']['schema'],
                    'verified': True, 'workers_enabled': False, 'document_paths_relocated': relocated_paths > 0,
                    'local_document_paths_relocated': relocated_paths,
                    'activation_requires_reconciliation': True, 'recovery_hold': True, 'snapshot_at': manifest['snapshot_at'],
                    'objects_remapped': len(replacements),
                    'storage_rebind_required': rebind_count,
                    'backup_schedules_disabled': disabled_schedules,'backup_jobs_review_required': reviewed_jobs,
                    'elapsed_seconds': round(time.monotonic() - started, 3)}
    except Exception:
        # Database may now be restored but is never erased/activated on a failed check.
        # Keep material for diagnosis when SQL restore has started.
        if not sql_started:
            for root in created:
                shutil.rmtree(root)
        raise
    finally:
        engine.dispose()
