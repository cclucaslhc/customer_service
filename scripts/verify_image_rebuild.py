"""Compare independently built OCI image manifest digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


def image_digest(archive: Path) -> str:
    with tarfile.open(archive, "r:*") as content:
        member = content.extractfile("index.json")
        if member is None:
            raise ValueError("OCI archive lacks index.json")
        index = json.load(member)
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("expected one OCI image manifest")
        digest = manifests[0].get("digest") if isinstance(manifests[0], dict) else None
        manifest_bytes = _verified_blob(content, digest)
        manifest = json.loads(manifest_bytes)
        descriptors = [manifest.get("config"), *manifest.get("layers", [])]
        if not descriptors or any(not isinstance(item, dict) for item in descriptors):
            raise ValueError("OCI image manifest has invalid descriptors")
        for descriptor in descriptors:
            _verified_blob(content, descriptor.get("digest"))
        return digest


def _verified_blob(archive: tarfile.TarFile, digest: object) -> bytes:
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid OCI blob digest")
    member = archive.extractfile(f"blobs/sha256/{digest[7:]}")
    if member is None:
        raise ValueError("OCI blob missing")
    content = member.read()
    if hashlib.sha256(content).hexdigest() != digest[7:]:
        raise ValueError("OCI blob hash mismatch")
    return content


def verify_rebuild(first: Path, second: Path) -> str:
    original = image_digest(first)
    rebuilt = image_digest(second)
    if original != rebuilt:
        raise ValueError(f"independent image rebuild differs: {original} != {rebuilt}")
    return original


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    print(f"Reproducible OCI image: {verify_rebuild(args.first, args.second)}")


if __name__ == "__main__":
    main()
