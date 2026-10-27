import io
import hashlib
import json
import tarfile

import pytest

from scripts.verify_image_rebuild import verify_rebuild


def oci_archive(path, layer):
    config = b"{}"
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    layer_digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    manifest_data = json.dumps({"config": {"digest": config_digest},
                                "layers": [{"digest": layer_digest}]}).encode()
    manifest_digest = "sha256:" + hashlib.sha256(manifest_data).hexdigest()
    data = json.dumps({"manifests": [{"digest": manifest_digest}]}).encode()
    with tarfile.open(path, "w") as output:
        for name, value in (("index.json", data),
                            ("blobs/sha256/" + manifest_digest[7:], manifest_data),
                            ("blobs/sha256/" + config_digest[7:], config),
                            ("blobs/sha256/" + layer_digest[7:], layer)):
            member = tarfile.TarInfo(name)
            member.size = len(value)
            output.addfile(member, io.BytesIO(value))
    return manifest_digest


def test_verify_image_rebuild_matches_manifest(tmp_path):
    first, second = tmp_path / "a.tar", tmp_path / "b.tar"
    digest = oci_archive(first, b"same layer")
    oci_archive(second, b"same layer")
    assert verify_rebuild(first, second) == digest


def test_verify_image_rebuild_rejects_mismatch(tmp_path):
    first, second = tmp_path / "a.tar", tmp_path / "b.tar"
    oci_archive(first, b"first layer")
    oci_archive(second, b"other layer")
    with pytest.raises(ValueError, match="differs"):
        verify_rebuild(first, second)
