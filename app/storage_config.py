"""Explicit approved storage targets, tenant-bound encryption and atomic audit."""
from dataclasses import dataclass
import hashlib
import json
import re
import uuid
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from app.object_store import S3Credentials, S3ObjectStore, ObjectStoreError
from app.persistence.schema import metadata
from app.persistence.identity_repository import IdentityRepository
from app.recovery import require_operational


class StorageConflict(ValueError):
    pass


@dataclass(frozen=True)
class StorageTarget:
    name: str
    endpoint: str
    bucket: str
    region: str

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps([self.name,self.endpoint,self.bucket,self.region],separators=(',',':')).encode()).hexdigest()


class StoragePolicy:
    def __init__(self, targets, *, loopback_tests=False):
        self.targets = {target.name:target for target in targets}
        if len(self.targets) != len(targets) or not self.targets:
            raise ValueError('Explicit unique approved storage targets required')
        self.loopback_tests = loopback_tests
        for target in targets:
            if not re.fullmatch('[a-zA-Z0-9_-]{1,64}',target.name):
                raise ValueError('Invalid approved storage target name')
            # Validation only; no client construction, credentials discovery or I/O.
            S3ObjectStore(endpoint=target.endpoint,bucket=target.bucket,region=target.region,
                credentials=S3Credentials('validation-only','validation-only'),client=object(),
                allow_loopback=loopback_tests,test_skip_public_block_api=loopback_tests)

    def target(self, name):
        try:
            return self.targets[name]
        except (KeyError, TypeError):
            raise ObjectStoreError('Storage target not approved by deployment policy') from None

    def build(self, target, credentials):
        return S3ObjectStore(endpoint=target.endpoint,bucket=target.bucket,region=target.region,credentials=credentials,
            allow_loopback=self.loopback_tests,test_skip_public_block_api=self.loopback_tests)

    @classmethod
    def from_file(cls, path):
        from pathlib import Path
        path = Path(path)
        if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError('Explicit bounded approved policy file required')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,list) or not 1 <= len(value) <= 32:
            raise ValueError('Bounded approved target list required')
        return cls([StorageTarget(**row) for row in value])


class StorageConfiguration:
    def __init__(self, engine, secrets, policy):
        if engine.dialect.name != 'postgresql':
            raise ValueError('Storage configuration requires authenticated PostgreSQL')
        self.engine, self.secrets, self.policy = engine,secrets,policy

    def _principal(self, db, token, permission):
        require_operational(db)
        p = IdentityRepository(self.engine).resolve(token,connection=db)
        p.require(permission)
        return p

    @staticmethod
    def _label(target, revision):
        return f'object-storage:v1:{target.fingerprint}:{revision}'

    def describe(self, token):
        table = metadata.tables['storage_configs']
        with self.engine.connect() as db:
            p = self._principal(db,token,'config.manage')
            row = db.execute(sa.select(table.c.target,table.c.revision,table.c.state).where(table.c.tenant_id == uuid.UUID(p.tenant_id))).mappings().first()
            return {'enabled':True,'configured':row is not None,'target':row['target'] if row else None,
                'revision':row['revision'] if row else 0,'targets':list(self.policy.targets) if self.policy else [],
                'recovery_rebind':bool(row and row['state'] == 'recovery_rebind'),
                'policy_configured':self.policy is not None}

    def save(self, token, *, target_name, expected_revision, access_key, secret_key, session_token=None):
        if self.policy is None:
            raise ObjectStoreError('Explicit deployment storage policy required')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid storage revision')
        target = self.policy.target(target_name)
        credentials = S3Credentials(access_key,secret_key,session_token)
        table = metadata.tables['storage_configs']
        # Permission before network or master key creation.
        with self.engine.connect() as db:
            p = self._principal(db,token,'config.manage')
            row = db.execute(sa.select(table).where(table.c.tenant_id == uuid.UUID(p.tenant_id))).mappings().first()
            if (row['revision'] if row else 0) != expected_revision:
                raise StorageConflict('Storage configuration changed; refresh before saving')
            if row and row['state'] == 'active' and (row['target'] != target.name or row['target_fingerprint'] != target.fingerprint):
                raise StorageConflict('Changing storage target requires a separate referenced-object migration')
        client = self.policy.build(target,credentials)
        try:
            client.check_ready()
        finally:
            client.close()
        revision = expected_revision+1
        # Recheck after network before touching secrets. No DB transaction spans I/O.
        with self.engine.connect() as db:
            self._principal(db,token,'config.manage')
        cipher,nonce = self.secrets.encrypt(json.dumps({'access_key':access_key,'secret_key':secret_key,
            'session_token':session_token}),self._label(target,revision),tenant_id=p.tenant_id)
        with self.engine.begin() as db:
            current = self._principal(db,token,'config.manage')
            if current.tenant_id != p.tenant_id:
                raise StorageConflict('Storage configuration identity changed')
            db.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':1129533505})
            row = db.execute(sa.select(table).where(table.c.tenant_id == uuid.UUID(p.tenant_id)).with_for_update()).mappings().first()
            if (row['revision'] if row else 0) != expected_revision:
                raise StorageConflict('Storage configuration changed; refresh before saving')
            if row and row['state'] == 'recovery_rebind':
                from app.backup import _references
                references = _references(db,include_objects=True)
                if any(r.get('tenant_id') == p.tenant_id and r['object']['bucket'] != target.bucket
                       for r in references if 'object' in r):
                    raise StorageConflict('Approved target does not match restored object references')
            values = {'tenant_id':uuid.UUID(p.tenant_id),'target':target.name,'target_fingerprint':target.fingerprint,
                'revision':revision,'state':'active','encrypted_credentials':cipher,'nonce':nonce,'updated_at':sa.func.clock_timestamp()}
            db.execute(insert(table).values(**values).on_conflict_do_update(index_elements=[table.c.tenant_id],set_={k:v for k,v in values.items() if k != 'tenant_id'}))
            db.execute(metadata.tables['audit_logs'].insert().values(id=uuid.uuid4(),tenant_id=uuid.UUID(p.tenant_id),
                actor_id=uuid.UUID(p.user_id),actor=p.user_id,action='storage.configure',entity_type='storage',
                entity_id=p.tenant_id,summary='Approved storage credentials updated',metadata_json={'revision':revision},
                created_at=sa.func.clock_timestamp()))
        return self.describe(token)

    def resolve(self, token):
        table = metadata.tables['storage_configs']
        with self.engine.connect() as db:
            p = self._principal(db,token,'knowledge.read')
            row = db.execute(sa.select(table).where(table.c.tenant_id == uuid.UUID(p.tenant_id))).mappings().first()
            if not row:
                return None
            if row['state'] != 'active':
                raise ObjectStoreError('Restored storage credentials require explicit administrator rebind')
        if self.policy is None:
            raise ObjectStoreError('Explicit deployment storage policy required')
        target = self.policy.target(row['target'])
        if target.fingerprint != row['target_fingerprint']:
            raise ObjectStoreError('Storage deployment target changed; migration required')
        try:
            values = json.loads(self.secrets.decrypt(row['encrypted_credentials'],row['nonce'],
                self._label(target,row['revision']),tenant_id=p.tenant_id))
            credentials = S3Credentials(**values)
        except Exception:
            raise ObjectStoreError('Storage credentials unavailable or binding invalid') from None
        with self.engine.connect() as db:
            latest = self._principal(db,token,'knowledge.read')
            now = db.execute(sa.select(table.c.revision).where(table.c.tenant_id == uuid.UUID(p.tenant_id))).scalar_one_or_none()
            if latest.tenant_id != p.tenant_id or now != row['revision']:
                raise ObjectStoreError('Storage configuration changed during resolution')
        return self.policy.build(target,credentials)
