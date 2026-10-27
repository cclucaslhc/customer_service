"""Private versioned S3 blobs; explicit credentials only, no public URL fallback."""
from dataclasses import dataclass, field
import base64
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
import uuid

from app.tracing import traced_object_operation


class ObjectStoreError(RuntimeError):
    def __init__(self, message='Private object storage unavailable or integrity invalid'):
        super().__init__(message)


@dataclass(frozen=True)
class S3Credentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if (not isinstance(self.access_key,str) or not isinstance(self.secret_key,str)
                or not self.access_key or not self.secret_key):
            raise ValueError('Explicit object credentials required')


@dataclass(frozen=True)
class ObjectReference:
    bucket: str
    key: str
    version_id: str
    size: int
    sha256: str

    @classmethod
    def from_dict(cls, value):
        try:
            if set(value) != {'bucket','key','version_id','size','sha256'}:
                raise ValueError()
            return cls(**value)
        except Exception:
            raise ObjectStoreError('Invalid private object reference') from None

    def to_dict(self):
        return {name: getattr(self,name) for name in ('bucket','key','version_id','size','sha256')}


class S3ObjectStore:
    MAX_BYTES = 100 * 1024 * 1024

    def __init__(self, *, endpoint: str, bucket: str, region: str, credentials: S3Credentials,
                 allow_loopback=False, test_skip_public_block_api=False, client=None):
        parsed = urlsplit(endpoint)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'','/'} or not region or not credentials):
            raise ValueError('Explicit trusted S3 endpoint required')
        loopback = parsed.hostname == 'localhost'
        try:
            host = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            host = None
        if host is not None:
            loopback = host.is_loopback
            if host.is_link_local or host.is_unspecified or host.is_multicast:
                raise ValueError('Unsafe S3 endpoint')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback and allow_loopback):
            raise ValueError('S3 requires TLS; explicit loopback only for isolated tests')
        if test_skip_public_block_api and not (loopback and allow_loopback):
            raise ValueError('Public blocking API exemption is isolated loopback test-only')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
            raise ValueError('Invalid private bucket')
        self.bucket = bucket
        self.endpoint,self.region = endpoint.rstrip('/'),region
        self.test_skip_public_block_api = test_skip_public_block_api
        if client is None:
            import boto3
            from botocore.config import Config
            session = boto3.session.Session(aws_access_key_id=credentials.access_key,
                aws_secret_access_key=credentials.secret_key, aws_session_token=credentials.session_token,
                region_name=region)
            client = session.client('s3', endpoint_url=endpoint, verify=True,
                config=Config(signature_version='s3v4', s3={'addressing_style':'path'}, proxies={},
                    connect_timeout=3, read_timeout=15, retries={'total_max_attempts':1,'mode':'standard'}))
        self.client = client

    @property
    def policy_fingerprint(self):
        """Approved destination identity, independent of credential rotation."""
        value = ['private-versioned-s3-v1',self.endpoint,self.bucket,self.region]
        return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()

    def _call(self, name, **kwargs):
        try:
            return getattr(self.client,name)(**kwargs)
        except Exception:
            # SDK errors may contain endpoint/signature/credential/body details.
            raise ObjectStoreError() from None

    @traced_object_operation('check_ready')
    def check_ready(self):
        if self._call('get_bucket_versioning', Bucket=self.bucket).get('Status') != 'Enabled':
            raise ObjectStoreError('Private bucket versioning must be enabled explicitly')
        if self.test_skip_public_block_api:
            grants = self._call('get_bucket_acl', Bucket=self.bucket).get('Grants',[])
            if not grants or any(grant.get('Grantee',{}).get('Type') != 'CanonicalUser' for grant in grants):
                raise ObjectStoreError('Private test bucket ACL required')
            return
        policy = self._call('get_public_access_block', Bucket=self.bucket).get('PublicAccessBlockConfiguration', {})
        if any(policy.get(name) is not True for name in (
            'BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')):
            raise ObjectStoreError('Private bucket public-access blocking is required')

    def _validate(self, tenant_id, reference):
        tenant = str(uuid.UUID(str(tenant_id)))
        if (reference.bucket != self.bucket or not isinstance(reference.key,str)
                or not reference.key.startswith(f'tenants/{tenant}/')
                or not re.fullmatch(r'[A-Za-z0-9_/-]+',reference.key) or '..' in reference.key
                or not isinstance(reference.version_id,str) or reference.version_id in {'','null'}
                or len(reference.version_id) > 1024 or type(reference.size) is not int
                or not 0 <= reference.size <= self.MAX_BYTES
                or not isinstance(reference.sha256,str) or not re.fullmatch('[0-9a-f]{64}',reference.sha256)):
            raise ObjectStoreError('Object reference outside tenant or invalid')

    @traced_object_operation('put')
    def put(self, tenant_id, data: bytes, *, kind: str):
        tenant = str(uuid.UUID(str(tenant_id)))
        if kind not in {'knowledge','assets','backup'} or not isinstance(data,bytes) or len(data) > self.MAX_BYTES:
            raise ObjectStoreError('Invalid private object payload')
        self.check_ready()
        digest = hashlib.sha256(data).hexdigest()
        key = f'tenants/{tenant}/{kind}/{uuid.uuid4().hex}/{digest}'
        result = self._call('put_object',Bucket=self.bucket,Key=key,Body=data,ContentLength=len(data),
            ContentType='application/octet-stream',ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
            Metadata={'sha256':digest})
        version = result.get('VersionId')
        reference = ObjectReference(self.bucket,key,version,len(data),digest)
        self._validate(tenant,reference)
        # Do not use ETag as a content digest or read an unversioned latest key.
        if self.get(tenant,reference) != data:
            raise ObjectStoreError('Uploaded object verification failed')
        return reference

    @traced_object_operation('get')
    def get(self, tenant_id, reference: ObjectReference) -> bytes:
        self._validate(tenant_id,reference)
        result = self._call('get_object',Bucket=self.bucket,Key=reference.key,VersionId=reference.version_id)
        body = result.get('Body')
        try:
            if (result.get('VersionId') != reference.version_id or result.get('ContentLength') != reference.size
                    or result.get('Metadata',{}).get('sha256') != reference.sha256 or body is None):
                raise ObjectStoreError()
            data = body.read(reference.size + 1)
            if len(data) != reference.size or hashlib.sha256(data).hexdigest() != reference.sha256:
                raise ObjectStoreError()
            return data
        except Exception:
            raise ObjectStoreError() from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass

    def close(self):
        self.client.close()

    @traced_object_operation('list_versions')
    def require_empty_restore_bucket(self):
        self.check_ready()
        result = self._call('list_object_versions', Bucket=self.bucket, MaxKeys=1)
        if result.get('Versions') or result.get('DeleteMarkers') or result.get('IsTruncated'):
            raise ObjectStoreError('Restore requires an empty private versioned bucket')
