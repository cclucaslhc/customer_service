import hashlib

import pytest

from app.rag_model_manifest import WeightManifest, verify_weight


def test_local_weight_verifier_rejects_mismatch(tmp_path):
    path = tmp_path / 'model.safetensors'
    path.write_bytes(b'synthetic-weight')
    manifest = WeightManifest('synthetic', 'immutable', 'synthetic', path.name,
                              path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
    verify_weight(tmp_path, manifest)
    path.write_bytes(b'synthetic-weighu')
    with pytest.raises(ValueError, match='SHA-256'):
        verify_weight(tmp_path, manifest)
    path.write_bytes(b'short')
    with pytest.raises(ValueError, match='size'):
        verify_weight(tmp_path, manifest)
