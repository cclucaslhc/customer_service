"""Explicit isolated local PostgreSQL restore verification; never enables service."""
from pathlib import Path
import hashlib
import json
import copy
import re
import uuid
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from app.backup import restore_backup,checked_directory,_validated_url
from app.persistence.engine import build_engine
from app.object_store import ObjectStoreError


class ObjectRestoreTargets:
    """Explicit private per-attempt bucket provisioning; no bucket deletion/reuse.

    Template client is borrowed from caller. Its bucket names a reserved prefix,
    not a source bucket; it must have narrowly scoped isolated creation rights.
    """
    def __init__(self,template,*,isolated_object_environment_confirmed=False):
        if not isolated_object_environment_confirmed:
            raise ValueError('Explicit isolated object provisioning authorization required')
        if re.fullmatch('[a-z0-9][a-z0-9-]{2,28}[a-z0-9]',template.bucket) is None:
            raise ValueError('Verification bucket prefix must be 4-30 safe characters')
        self.template = template

    @property
    def policy_fingerprint(self):
        return hashlib.sha256(('isolated-object-restore-v1:'+self.template.policy_fingerprint).encode()).hexdigest()

    def provision(self,attempt_id,guard):
        attempt = uuid.UUID(str(attempt_id))
        target = copy.copy(self.template)  # Borrow client; caller closes once.
        target.bucket = self.template.bucket+'-'+attempt.hex
        guard()
        try:
            self.template.client.head_bucket(Bucket=target.bucket)
        except Exception as failure:
            response = getattr(failure,'response',{})
            if (response.get('ResponseMetadata',{}).get('HTTPStatusCode') != 404
                    or response.get('Error',{}).get('Code') not in {'404','NoSuchBucket','NotFound'}):
                raise ObjectStoreError('Verification bucket existence is unknown') from None
        else:
            raise ObjectStoreError('Verification bucket already exists; do not reuse')
        guard()
        options = {'Bucket':target.bucket}
        if target.region != 'us-east-1':
            options['CreateBucketConfiguration'] = {'LocationConstraint':target.region}
        target._call('create_bucket',**options)
        guard()
        target._call('put_bucket_versioning',Bucket=target.bucket,VersioningConfiguration={'Status':'Enabled'})
        guard()
        if not target.test_skip_public_block_api:
            target._call('put_public_access_block',Bucket=target.bucket,PublicAccessBlockConfiguration=dict.fromkeys(
                ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'),True))
        guard()
        target.require_empty_restore_bucket()
        guard()
        return target


class LocalRestoreVerifier:
    def __init__(self,*,admin_url,root:Path,isolated_environment_confirmed=False,object_targets=None):
        if not isolated_environment_confirmed:
            raise ValueError('Explicit isolated verification authorization required')
        _validated_url(admin_url)
        self.admin_url,self.root = admin_url,checked_directory(root,existing=True)
        self.object_targets = object_targets

    @property
    def policy_fingerprint(self):
        # Bind the approved destination and verifier implementation, not secrets.
        target = make_url(self.admin_url).set(password=None)
        value = {'kind':'local-postgresql-restore-v1',
                 'target':target.render_as_string(hide_password=True),'root':str(self.root)}
        if self.object_targets is not None:
            value['objects'] = self.object_targets.policy_fingerprint
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    def verify(self,*,attempt_id,package,plan,guard):
        attempt_id = uuid.UUID(str(attempt_id))
        name = 'cs_backup_verify_'+attempt_id.hex
        run_root = self.root/attempt_id.hex
        # A prior unknown attempt is never overwritten, dropped or retried.
        if run_root.exists():
            raise ValueError('Verification attempt already exists')
        guard()
        run_root.mkdir(mode=0o700)
        admin = build_engine(self.admin_url,pool_size=1)
        try:
            object_store = self.object_targets.provision(attempt_id,guard) if self.object_targets is not None else None
            guard()
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
                db.exec_driver_sql(f'CREATE DATABASE "{name}"')
            target_url = make_url(self.admin_url).set(database=name).render_as_string(hide_password=False)
            report = restore_backup(url=target_url,binary_dir=plan.binary_dir,source=package,
                knowledge_root=run_root/'knowledge',asset_root=run_root/'assets',key_path=plan.key_path,
                scratch_root=plan.scratch_root,isolated_confirmed=True,object_store=object_store)
            guard()
            if not report['verified'] or not report['recovery_hold'] or report['workers_enabled']:
                raise ValueError('Restore verification did not retain execution hold')
            # Only this exact successfully verified target is eligible for drop.
            # Failed/unknown validation remains isolated for authorized review.
            assert name == 'cs_backup_verify_'+attempt_id.hex
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
                db.exec_driver_sql(f'DROP DATABASE "{name}"')
            result = {'restore_verified':True,'restore_elapsed_seconds':report['elapsed_seconds']}
            if object_store is not None:
                result['restore_bucket'] = object_store.bucket
            return result
        finally:
            # Plaintext verification files are retained under the explicit
            # private root; retention/privacy GC needs its own approved policy.
            admin.dispose()
