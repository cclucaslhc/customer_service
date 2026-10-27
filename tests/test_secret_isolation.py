"""Secret provisioning is explicit/lazy and uses only temporary synthetic keys."""

from pathlib import Path

import pytest

from app.security import SecretStore


def test_constructor_never_reads_or_provisions_secret(tmp_path, monkeypatch):
    path = tmp_path / "master.key"
    def forbidden(*args, **kwargs):
        raise AssertionError("Construction must not touch files")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        store = SecretStore(path=path)
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        store.decrypt(b"synthetic", b"synthetic", "deepseek")
    assert not path.exists()


def test_synthetic_encryption_keeps_provider_binding_and_reload(tmp_path):
    path = tmp_path / "secret" / "master.key"
    first = SecretStore(path=path)
    ciphertext, nonce = first.encrypt("synthetic-not-a-model-key", "deepseek")
    assert path.is_file() and len(path.read_bytes()) == 32
    second = SecretStore(path=path)
    assert second.decrypt(ciphertext, nonce, "deepseek") == "synthetic-not-a-model-key"
    with pytest.raises(Exception):
        second.decrypt(ciphertext, nonce, "other-provider")


def test_tenant_aad_cannot_be_replayed_between_tenants_or_as_legacy(tmp_path):
    import uuid
    from cryptography.exceptions import InvalidTag
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    store = SecretStore(path=tmp_path / "synthetic-master.key")
    encrypted, nonce = store.encrypt("synthetic-not-a-model-key", "deepseek", tenant_id=a)
    assert store.decrypt(encrypted, nonce, "deepseek", tenant_id=a) == "synthetic-not-a-model-key"
    for provider, tenant in (("deepseek", b), ("deepseek", None), ("openai", a)):
        with pytest.raises(InvalidTag):
            store.decrypt(encrypted, nonce, provider, tenant_id=tenant)
