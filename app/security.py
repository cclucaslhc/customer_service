import os
import threading
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.settings import MASTER_KEY_PATH


class SecretStore:
    def __init__(self, *, path: Path | None = None) -> None:
        # Importing/constructing the application must not read local secrets.
        self._path = path
        self._cached_path = None
        self._key = None
        self._lock = threading.RLock()

    @property
    def path(self):
        return self._path or MASTER_KEY_PATH

    def _get_key(self, *, create=False):
        with self._lock:
            path = self.path.resolve()
            if self._key is None or self._cached_path != path:
                if create and not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with path.open("xb") as file:
                            file.write(AESGCM.generate_key(bit_length=256))
                        os.chmod(path, 0o600)
                    except FileExistsError:
                        pass  # Another process provisioned the same master key.
                key = path.read_bytes()
                if len(key) != 32:
                    raise RuntimeError("主密钥文件格式无效")
                self._key, self._cached_path = key, path
            return self._key

    @staticmethod
    def _aad(provider, tenant_id):
        return provider.encode() if tenant_id is None else f"cs-provider:v1:{uuid.UUID(str(tenant_id))}:{provider}".encode()

    def encrypt(self, plaintext: str, provider: str, *, tenant_id=None) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._get_key(create=True)).encrypt(nonce, plaintext.encode(), self._aad(provider, tenant_id))
        return ciphertext, nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, provider: str, *, tenant_id=None) -> str:
        return AESGCM(self._get_key()).decrypt(nonce, ciphertext, self._aad(provider, tenant_id)).decode()
