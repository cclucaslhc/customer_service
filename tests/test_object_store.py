"""S3 component tests; local real-service tests never use environment credentials."""
import hashlib
from io import BytesIO
import os
import uuid
from dataclasses import replace

import pytest
boto3 = pytest.importorskip('boto3', reason='Install production dependencies for S3 acceptance')
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

from app.object_store import S3Credentials, S3ObjectStore, ObjectReference, ObjectStoreError


class Client:
    """Fault injection only, not evidence of actual S3 semantics."""
    def __init__(self):
        self.versioning = 'Enabled'
        self.blocked = True
        self.data = b'Synthetic knowledge'
        self.version = 'v-synthetic'
        self.calls = []
        self.body = None
    def get_bucket_versioning(self, **kwargs):
        self.calls.append('versioning')
        return {'Status':self.versioning}
    def get_public_access_block(self, **kwargs):
        return {'PublicAccessBlockConfiguration':dict.fromkeys(
            ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'),self.blocked)}
    def put_object(self, **kwargs):
        self.calls.append('put')
        self.data = kwargs['Body']
        return {'VersionId':self.version}
    def get_object(self, **kwargs):
        self.calls.append('get')
        self.body = BytesIO(self.data)
        return {'VersionId':self.version,'ContentLength':len(self.data),
                'Metadata':{'sha256':hashlib.sha256(self.data).hexdigest()},'Body':self.body}


def store(client=None, **kwargs):
    return S3ObjectStore(endpoint='https://objects.example.invalid',bucket='synthetic-private',region='us-east-1',
        credentials=S3Credentials('synthetic-access','synthetic-secret-only'), client=client or Client(), **kwargs)


def reference(tenant, **changes):
    return replace(ObjectReference('synthetic-private',f'tenants/{tenant}/knowledge/unique/hash','v-synthetic',
        len(b'Synthetic knowledge'),hashlib.sha256(b'Synthetic knowledge').hexdigest()), **changes)


def test_secret_repr_and_explicit_credentials():
    assert 'synthetic-access' not in repr(S3Credentials('synthetic-access','synthetic-secret-only'))
    with pytest.raises(ValueError):
        S3Credentials('', '')


@pytest.mark.parametrize('endpoint', ('http://objects.example.invalid','http://127.0.0.1:59000',
    'https://169.254.50.10','https://user:password@objects.example.invalid','https://objects.example.invalid?redirect=yes'))
def test_endpoints_fail_closed(endpoint):
    with pytest.raises(ValueError):
        S3ObjectStore(endpoint=endpoint,bucket='synthetic-private',region='us-east-1',
            credentials=S3Credentials('synthetic','synthetic'),client=Client())


def test_nonlocal_public_api_exemption_is_rejected():
    with pytest.raises(ValueError):
        store(test_skip_public_block_api=True)


@pytest.mark.parametrize('change', ({'bucket':'other-private'},{'version_id':'null'}, {'version_id':''},
    {'key':'tenants/foreign/knowledge/hash'},{'size':-1},{'sha256':'not-a-hash'}))
def test_references_are_validated_before_network(change):
    client = Client()
    tenant = str(uuid.uuid4())
    with pytest.raises(ObjectStoreError):
        store(client).get(tenant,reference(tenant,**change))
    assert not client.calls


def test_versioning_and_privacy_refusal_precedes_upload():
    client = Client()
    selected = store(client)
    client.versioning = 'Suspended'
    with pytest.raises(ObjectStoreError):
        selected.put(str(uuid.uuid4()), b'Synthetic', kind='knowledge')
    client.versioning = 'Enabled'
    client.blocked = False
    with pytest.raises(ObjectStoreError):
        selected.put(str(uuid.uuid4()), b'Synthetic', kind='knowledge')
    assert 'put' not in client.calls


def test_corruption_is_rejected_and_response_body_closed():
    tenant = str(uuid.uuid4())
    client = Client()
    client.data = b'Corrupt synthetic'
    with pytest.raises(ObjectStoreError):
        store(client).get(tenant, reference(tenant))
    assert client.body.closed


def test_sdk_failures_do_not_expose_credential_or_endpoint_details():
    client = Client()
    def fail(**kwargs):
        raise RuntimeError('synthetic-secret-only https://private-sensitive.invalid')
    client.get_bucket_versioning = fail
    with pytest.raises(ObjectStoreError) as error:
        store(client).check_ready()
    assert 'synthetic-secret-only' not in str(error.value) and 'private-sensitive' not in str(error.value)


@pytest.fixture
def real_s3():
    endpoint = os.environ.get('TEST_S3_ENDPOINT')
    if not endpoint:
        pytest.skip('TEST_S3_ENDPOINT unset; real S3 remains unverified')
    assert endpoint == 'http://127.0.0.1:59000'
    client = boto3.client('s3',endpoint_url=endpoint,region_name='us-east-1',
        aws_access_key_id='synthetic-s3-access',aws_secret_access_key='synthetic-s3-secret-only',
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},proxies={},
                      connect_timeout=3,read_timeout=5,retries={'total_max_attempts':1}))
    bucket = 'cs-test-s3-' + uuid.uuid4().hex
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket,VersioningConfiguration={'Status':'Enabled'})
    selected = S3ObjectStore(endpoint=endpoint,bucket=bucket,region='us-east-1',
        credentials=S3Credentials('synthetic-s3-access','synthetic-s3-secret-only'),allow_loopback=True,
        test_skip_public_block_api=True)
    try:
        yield selected, client
    finally:
        selected.close()
        # Only this exact unique disposable bucket; no recursive/foreign deletion.
        versions = client.list_object_versions(Bucket=bucket)
        objects = [{'Key':row['Key'],'VersionId':row['VersionId']} for row in
                   versions.get('Versions',[]) + versions.get('DeleteMarkers',[])]
        if objects:
            client.delete_objects(Bucket=bucket,Delete={'Objects':objects})
        client.delete_bucket(Bucket=bucket)
        client.close()


def test_real_versioned_put_get_survives_overwrite_and_rejects_foreign_tenant(real_s3):
    selected, client = real_s3
    tenant = str(uuid.uuid4())
    ref = selected.put(tenant,b'Synthetic private knowledge',kind='knowledge')
    assert ref.version_id not in {'','null'} and selected.get(tenant,ref) == b'Synthetic private knowledge'
    client.put_object(Bucket=selected.bucket,Key=ref.key,Body=b'Synthetic later overwrite')
    assert selected.get(tenant,ref) == b'Synthetic private knowledge'
    with pytest.raises(ObjectStoreError):
        selected.get(str(uuid.uuid4()),ref)


def test_real_anonymous_and_wrong_credentials_are_denied(real_s3):
    selected, client = real_s3
    tenant = str(uuid.uuid4())
    ref = selected.put(tenant,b'Synthetic private image',kind='assets')
    for signature, access, secret in ((UNSIGNED,'synthetic-unused','synthetic-unused'),
                                       ('s3v4','synthetic-invalid','synthetic-invalid')):
        outsider = boto3.client('s3',endpoint_url='http://127.0.0.1:59000',region_name='us-east-1',
            aws_access_key_id=access,aws_secret_access_key=secret,
            config=Config(signature_version=signature,s3={'addressing_style':'path'},proxies={},
                          retries={'total_max_attempts':1},connect_timeout=3,read_timeout=5))
        try:
            with pytest.raises(ClientError):
                outsider.get_object(Bucket=selected.bucket,Key=ref.key,VersionId=ref.version_id)
        finally:
            outsider.close()


def test_real_deleted_version_does_not_fall_back_to_latest(real_s3):
    selected, client = real_s3
    tenant = str(uuid.uuid4())
    ref = selected.put(tenant,b'Synthetic original',kind='knowledge')
    client.put_object(Bucket=selected.bucket,Key=ref.key,Body=b'Synthetic replacement')
    client.delete_object(Bucket=selected.bucket,Key=ref.key,VersionId=ref.version_id)
    with pytest.raises(ObjectStoreError):
        selected.get(tenant,ref)


def test_real_public_access_block_capability_does_not_silently_downgrade(real_s3):
    """Interop evidence: unsupported MinIO is NOT a production approval."""
    selected,client=real_s3
    required=dict.fromkeys(('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'),True)
    try:
        client.put_public_access_block(Bucket=selected.bucket,PublicAccessBlockConfiguration=required)
        actual=client.get_public_access_block(Bucket=selected.bucket)['PublicAccessBlockConfiguration']
    except ClientError as exc:
        code=exc.response.get('Error',{}).get('Code')
        assert code in {'NotImplemented','NoSuchPublicAccessBlockConfiguration','XNotImplemented','MalformedXML'}, code
        # Inject the real isolated client to test the normal gate without its
        # loopback exemption. This is not evidence of remote TLS transport.
        strict=S3ObjectStore(
            endpoint='https://synthetic.invalid',bucket=selected.bucket,region='us-east-1',
            credentials=S3Credentials('synthetic-s3-access','synthetic-s3-secret-only'),client=client)
        with pytest.raises(ObjectStoreError): strict.check_ready()
        print('public_access_block=unverified_or_incompatible; production_gate=refused; code='+str(code))
    else:
        assert actual==required
        print('public_access_block=supported; remote_TLS_and_cross_node_acceptance=outstanding')
