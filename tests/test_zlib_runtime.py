"""Fixed native zlib replaces real files without hiding an installed package."""
import json
import os
from pathlib import Path
import pytest
from scripts.zlib_runtime import component,validate_component,enrich,prepare_root,PURL


def test_fixed_zlib_source_and_binary_are_required():
    value=component(b'synthetic fixed library')
    assert validate_component(value)==value
    value['version']='1.3.1'
    with pytest.raises(ValueError): validate_component(value)


def test_zlib_inventory_refuses_stale_debian_or_conflicting_binary(tmp_path):
    sbom,native=tmp_path/'sbom.json',tmp_path/'native.json'
    native.write_text(json.dumps({'components':[component(b'new library')]}))
    sbom.write_text(json.dumps({'components':[{'purl':'pkg:deb/debian/zlib1g@old'}]}))
    with pytest.raises(ValueError): enrich(sbom,native)
    sbom.write_text(json.dumps({'components':[]}))
    enrich(sbom,native)
    enrich(sbom,native)
    assert json.loads(sbom.read_text())['components']==[component(b'new library')]
    sbom.write_text(json.dumps({'components':[component(b'different binary')]}))
    with pytest.raises(ValueError): enrich(sbom,native)


def test_build_root_must_be_explicit_and_narrow(tmp_path):
    with pytest.raises(ValueError): prepare_root(tmp_path,tmp_path/'not-read')
    with pytest.raises(ValueError): prepare_root(Path('runtime-root'),tmp_path/'not-read')


def staging_root(tmp_path):
    root=tmp_path/'runtime-root'
    directory=root/'usr/lib/x86_64-linux-gnu'
    directory.mkdir(parents=True)
    status=root/'var/lib/dpkg/status.d'
    status.mkdir(parents=True)
    (status/'zlib1g').write_text('Package: zlib1g\nVersion: old\n')
    (status/'zlib1g.md5sums').write_text('synthetic old provenance')
    (status/'libc6').write_text('synthetic unrelated package retained')
    (directory/'libz.so.1.3.1').write_bytes(b'old library')
    (directory/'libz.so.1').symlink_to('libz.so.1.3.1')
    replacement=tmp_path/'new-library.so'
    replacement.write_bytes(b'\x7fELFsynthetic new library')
    return root,directory,status,replacement


@pytest.mark.skipif(os.name!='posix',reason='Linux image staging uses POSIX symlinks')
def test_replacement_removes_only_superseded_actual_package(tmp_path):
    root,directory,status,replacement=staging_root(tmp_path)
    prepare_root(root,replacement)
    assert not (directory/'libz.so.1.3.1').exists()
    assert not (status/'zlib1g').exists() and not (status/'zlib1g.md5sums').exists()
    assert (status/'libc6').read_text()=='synthetic unrelated package retained'
    assert (directory/'libz.so.1').read_bytes()==replacement.read_bytes()
    native=json.loads((root/'usr/share/customer-service/native/zlib.cdx.json').read_text())
    assert native['components']==[component(replacement.read_bytes())]


@pytest.mark.skipif(os.name!='posix',reason='Linux image staging uses POSIX symlinks')
def test_unexpected_layout_is_rejected_before_any_deletion(tmp_path):
    root,directory,status,replacement=staging_root(tmp_path)
    (directory/'libz.so.1').unlink()
    (directory/'libz.so.1').symlink_to('/unapproved/target')
    with pytest.raises(ValueError): prepare_root(root,replacement)
    assert (directory/'libz.so.1.3.1').read_bytes()==b'old library'
    assert (status/'zlib1g').is_file()


def test_build_and_ci_retain_upstream_zlib_coverage():
    root=Path(__file__).resolve().parents[1]
    build=(root/'Dockerfile').read_text()
    assert 'zlib-1.4.tar.gz' in build and 'make test' in build
    assert 'prepare-root /runtime-root /build/libz.so.1.4' in build
    assert 'FROM scratch\nCOPY --from=runtime-root /runtime-root/ /' in build
    workflow=(root/'.github/workflows/verify.yml').read_text()
    assert 'scripts/zlib_runtime.py probe' in workflow
    assert 'scripts/zlib_runtime.py enrich' in workflow
