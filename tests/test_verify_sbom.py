import json

import pytest

from scripts.verify_sbom import verify_sbom


def test_verify_sbom_requires_both_runtime_package_types(tmp_path):
    path = tmp_path / "sbom.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {"purl": "pkg:deb/debian/libxml2@2.0"},
                    {"purl": "pkg:deb/debian/libgcc-s1@14.2.0"},
                    {"purl": "pkg:deb/debian/libffi8@3.4.8"},
                    {"purl": "pkg:deb/debian/libsqlite3-0@3.46.1"},
                    {"purl": "pkg:deb/debian/libstdc%2B%2B6@14.2.0"},
                    {"purl": "pkg:pypi/pydantic-ai-slim@2.0"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert verify_sbom(path) == (5, 1)

    path.write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no components"):
        verify_sbom(path)


def test_verify_sbom_rejects_missing_inventory(tmp_path):
    path = tmp_path / "sbom.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [{"purl": "pkg:pypi/pydantic-ai-slim@2.0"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Debian and Python"):
        verify_sbom(path)

    path.write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6",
        "components": [{"purl": "pkg:deb/debian/libgcc-s1@14.2.0"},
                       {"purl": "pkg:pypi/pydantic-ai-slim@2.0"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="copied native runtime"):
        verify_sbom(path)
