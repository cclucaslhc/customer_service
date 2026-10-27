"""Provisioning fault injection only; real S3 tests live in scheduler_objects."""
from pathlib import Path
import uuid

import pytest
from botocore.exceptions import ClientError

from app.backup_verifier import ObjectRestoreTargets,LocalRestoreVerifier
from app.object_store import S3ObjectStore,S3Credentials,ObjectStoreError


class ProvisionClient:
    def __init__(self,*,exists=False,head_code='404',failure=None):
        self.exists,self.head_code,self.failure = exists,head_code,failure
        self.calls=[]; self.versioning=''; self.block={}
    def head_bucket(self,**kwargs):
        self.calls.append(('head',kwargs['Bucket']))
        if not self.exists:
            raise ClientError({'Error':{'Code':self.head_code},'ResponseMetadata':{'HTTPStatusCode':int(self.head_code)}},'HeadBucket')
    def create_bucket(self,**kwargs):
        self.calls.append(('create',kwargs))
        self.exists=True
        if self.failure == 'create':
            raise RuntimeError('synthetic-sensitive unknown acknowledgement')
    def put_bucket_versioning(self,**kwargs):
        self.calls.append(('version',kwargs['Bucket']))
        self.versioning=kwargs['VersioningConfiguration']['Status']
    def put_public_access_block(self,**kwargs):
        self.calls.append(('private',kwargs['Bucket']))
        if self.failure == 'private':
            raise RuntimeError('synthetic-sensitive IAM failure')
        self.block=kwargs['PublicAccessBlockConfiguration']
    def get_bucket_versioning(self,**kwargs):
        return {'Status':self.versioning}
    def get_public_access_block(self,**kwargs):
        return {'PublicAccessBlockConfiguration':self.block}
    def list_object_versions(self,**kwargs):
        self.calls.append(('empty',kwargs['Bucket']))
        return {}


def template(client,*,prefix='cs-verify',region='us-east-1'):
    return S3ObjectStore(endpoint='https://objects.example.invalid',bucket=prefix,region=region,
        credentials=S3Credentials('synthetic-only','synthetic-only'),client=client)


def test_provision_exact_attempt_private_versioned_bucket_and_borrowed_client():
    client=ProvisionClient(); selected=template(client)
    targets=ObjectRestoreTargets(selected,isolated_object_environment_confirmed=True)
    attempt=uuid.uuid4(); checks=[]
    result=targets.provision(attempt,lambda:checks.append(True))
    assert result.bucket == 'cs-verify-'+attempt.hex and selected.bucket == 'cs-verify'
    assert result.client is selected.client and client.versioning == 'Enabled' and all(client.block.values())
    assert len(checks) == 6 and [call[0] for call in client.calls] == ['head','create','version','private','empty']
    assert client.calls[1][1] == {'Bucket':result.bucket}


@pytest.mark.parametrize('exists,code',((True,'404'),(False,'403'),(False,'500')))
def test_existing_or_unknown_target_is_never_created_or_reused(exists,code):
    client=ProvisionClient(exists=exists,head_code=code)
    targets=ObjectRestoreTargets(template(client),isolated_object_environment_confirmed=True)
    with pytest.raises(ObjectStoreError):
        targets.provision(uuid.uuid4(),lambda:None)
    assert [call[0] for call in client.calls] == ['head']


@pytest.mark.parametrize('stage',('create','private'))
def test_unknown_create_or_privacy_failure_retains_resource_without_sensitive_echo(stage):
    client=ProvisionClient(failure=stage)
    targets=ObjectRestoreTargets(template(client),isolated_object_environment_confirmed=True)
    with pytest.raises(ObjectStoreError) as failure:
        targets.provision(uuid.uuid4(),lambda:None)
    assert client.exists and 'synthetic-sensitive' not in str(failure.value)
    assert not any(call[0] == 'delete' for call in client.calls)


def test_lost_guard_after_create_stops_before_versioning_and_keeps_bucket():
    client=ProvisionClient()
    targets=ObjectRestoreTargets(template(client),isolated_object_environment_confirmed=True)
    checks=[]
    def guard():
        checks.append(True)
        if len(checks) == 3:
            raise RuntimeError('Synthetic lease lost')
    with pytest.raises(RuntimeError,match='lease lost'):
        targets.provision(uuid.uuid4(),guard)
    assert client.exists and [call[0] for call in client.calls] == ['head','create']


def test_verifier_policy_binds_object_target_and_requires_explicit_provisioning(tmp_path):
    selected=template(ProvisionClient())
    with pytest.raises(ValueError,match='authorization'):
        ObjectRestoreTargets(selected)
    for prefix in ('a.b','a'*31):
        with pytest.raises(ValueError,match='prefix'):
            ObjectRestoreTargets(template(ProvisionClient(),prefix=prefix),isolated_object_environment_confirmed=True)
    options={'admin_url':'postgresql+psycopg://synthetic:synthetic-only@127.0.0.1/synthetic',
        'root':tmp_path,'isolated_environment_confirmed':True}
    local=LocalRestoreVerifier(**options)
    objects=LocalRestoreVerifier(**options,object_targets=ObjectRestoreTargets(selected,isolated_object_environment_confirmed=True))
    assert local.policy_fingerprint != objects.policy_fingerprint
    other=LocalRestoreVerifier(**options,object_targets=ObjectRestoreTargets(
        template(ProvisionClient(),prefix='cs-other'),isolated_object_environment_confirmed=True))
    assert other.policy_fingerprint != objects.policy_fingerprint


def test_nondefault_region_is_bound_to_create_request():
    client=ProvisionClient()
    targets=ObjectRestoreTargets(template(client,region='eu-west-1'),isolated_object_environment_confirmed=True)
    targets.provision(uuid.uuid4(),lambda:None)
    assert client.calls[1][1]['CreateBucketConfiguration'] == {'LocationConstraint':'eu-west-1'}
