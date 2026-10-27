"""Explicit operator-supplied backup credentials, never AWS environment discovery."""
import json

from app.backup import BackupError
from app.object_store import S3Credentials
from app.storage_config import StoragePolicy


def _credentials(value):
    if not isinstance(value,dict) or set(value) - {'access_key','secret_key','session_token'}:
        raise ValueError()
    if not all(isinstance(value.get(k),str) and 0 < len(value[k]) <= 4096 for k in ('access_key','secret_key')):
        raise ValueError()
    token = value.get('session_token')
    if token is not None and (not isinstance(token,str) or len(token) > 4096):
        raise ValueError()
    return S3Credentials(**value)


def operator_stores(*,policy_path,targets,credential_stream):
    """One bounded stdin envelope, explicit role-specific independent credentials.

    Caller owns successful clients; partial construction closes every owned client.
    No AWS/application/environment credential discovery or object mutation.
    """
    if (policy_path is None or not isinstance(targets,dict) or not targets
            or set(targets)-{'source','archive','verification','catalog'} or credential_stream is None):
        raise BackupError('Explicit backup storage roles required')
    policy = StoragePolicy.from_file(policy_path)
    approved = {role:policy.target(name) for role,name in targets.items()}
    try:
        raw = credential_stream.read(32769)
        if len(raw) > 32768 or len(raw.encode('utf-8')) > 32768:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value,dict) or set(value) != set(targets):
            raise ValueError()
        credentials = {role:_credentials(item) for role,item in value.items()}
    except Exception:
        raise BackupError('Invalid explicit backup credential envelope') from None
    stores = {}
    try:
        for role,target in approved.items():
            stores[role] = policy.build(target,credentials[role])
        return stores
    except Exception:
        for store in stores.values():
            try:
                store.close()
            except Exception:
                pass
        raise BackupError('Backup storage client construction failed') from None


def operator_store(*, policy_path, target_name, credential_stream):
    """Trusted maintenance entry point, not a tenant HTTP credential interface.

    Independent least-privilege backup/restore credentials are supplied on stdin.
    No argument, environment, application configuration or historical key fallback.
    The caller owns and closes the returned SDK client.
    """
    if policy_path is None or not target_name or credential_stream is None:
        raise BackupError('Explicit approved target and credential input required')
    policy = StoragePolicy.from_file(policy_path)
    target = policy.target(target_name)
    try:
        raw = credential_stream.read(16385)
        if len(raw) > 16384 or len(raw.encode('utf-8')) > 16384:
            raise ValueError()
        value = json.loads(raw)
        credentials = _credentials(value)
    except Exception:
        raise BackupError('Invalid explicit backup credential input') from None
    return policy.build(target,credentials)
