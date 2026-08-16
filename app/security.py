import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.settings import MASTER_KEY_PATH, ensure_directories


class SecretStore:
    def __init__(self) -> None:
        ensure_directories()
        if not MASTER_KEY_PATH.exists():
            MASTER_KEY_PATH.write_bytes(AESGCM.generate_key(bit_length=256))
            try:
                os.chmod(MASTER_KEY_PATH, 0o600)
            except OSError:
                pass
        self._key = MASTER_KEY_PATH.read_bytes()
        if len(self._key) != 32:
            raise RuntimeError("主密钥文件格式无效")

    def encrypt(self, plaintext: str, provider: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext.encode(), provider.encode())
        return ciphertext, nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, provider: str) -> str:
        return AESGCM(self._key).decrypt(nonce, ciphertext, provider.encode()).decode()
