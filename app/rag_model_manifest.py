"""Pinned local RAG weight identity; never trusts a mutable model tag."""

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True)
class WeightManifest:
    repository: str
    revision: str
    license_id: str
    filename: str
    size: int
    sha256: str


EMBEDDING_WEIGHT = WeightManifest(
    'Qwen/Qwen3-Embedding-0.6B',
    '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3',
    'apache-2.0', 'model.safetensors', 1191586416,
    '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd',
)
RERANKER_WEIGHT = WeightManifest(
    'Qwen/Qwen3-Reranker-0.6B',
    'e61197ed45024b0ed8a2d74b80b4d909f1255473',
    'apache-2.0', 'model.safetensors', 1191588280,
    '27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b',
)


def verify_weight(directory: Path, manifest: WeightManifest) -> None:
    path = directory / manifest.filename
    if path.is_symlink() or not path.is_file():
        raise ValueError('RAG weight file is missing or a symbolic link')
    if path.stat().st_size != manifest.size:
        raise ValueError('RAG weight size mismatch')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != manifest.sha256:
        raise ValueError('RAG weight SHA-256 mismatch')
