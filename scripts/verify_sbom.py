"""Fail CI when a generated image SBOM omits its runtime package inventories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote


COPIED_RUNTIME_PACKAGES = frozenset({"libgcc-s1", "libffi8", "libsqlite3-0", "libstdc++6"})


def verify_sbom(path: Path) -> tuple[int, int]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
        raise ValueError("expected CycloneDX 1.6 SBOM")
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("SBOM has no components")
    purls = [item.get("purl", "") for item in components if isinstance(item, dict)]
    debian = sum(purl.startswith("pkg:deb/debian/") for purl in purls)
    python = sum(purl.startswith("pkg:pypi/") for purl in purls)
    if not debian or not python:
        raise ValueError("SBOM must include Debian and Python runtime packages")
    names = {unquote(purl[len("pkg:deb/debian/"):].split("@", 1)[0])
             for purl in purls if purl.startswith("pkg:deb/debian/") and "@" in purl}
    if not COPIED_RUNTIME_PACKAGES <= names:
        raise ValueError("SBOM omits copied native runtime package provenance")
    return debian, python


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()
    debian, python = verify_sbom(args.sbom)
    print(f"SBOM verified: {debian} Debian and {python} Python components")


if __name__ == "__main__":
    main()
