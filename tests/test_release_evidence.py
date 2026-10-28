"""Standard Ed25519 and artifact binding; synthetic keys have no release trust."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_verify_image_rebuild import oci_archive

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_evidence',ROOT/'ops/release/evidence.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def evidence(tmp_path):
    image=tmp_path/'image.tar'
    digest=oci_archive(image,b'Synthetic immutable layer')
    config='sha256:'+hashlib.sha256(b'{}').hexdigest()
    sbom=tmp_path/'sbom.json'
    sbom.write_text(json.dumps({'bomFormat':'CycloneDX','specVersion':'1.6',
        'metadata':{'component':{'properties':[{'name':'aquasecurity:trivy:ImageID','value':config}]}},
        'components':[{'purl':'pkg:deb/debian/'+name+'@1'} for name in (
            'libgcc-s1','libffi8','libsqlite3-0','libstdc%2B%2B6')]+[{'purl':'pkg:pypi/synthetic@1'}]}))
    scan=tmp_path/'scan.json'
    scan.write_text(json.dumps({'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':config},
        'Results':[{'Class':'os-pkgs','Vulnerabilities':[]},{'Class':'lang-pkgs','Vulnerabilities':[]}]}))
    key=Ed25519PrivateKey.generate()
    private=tmp_path/'synthetic-private.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    private.chmod(0o600)
    public=tmp_path/'synthetic-public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    manifest,signature=tmp_path/'manifest.json',tmp_path/'signature.json'
    assert release.create(image,sbom,scan,manifest)==digest
    pin=release.sign(manifest,private,signature)
    return image,sbom,scan,manifest,signature,public,pin,digest


def test_signature_verifies_actual_image_inventory_and_scan(evidence):
    image,sbom,scan,manifest,signature,public,pin,digest=evidence
    assert release.verify(manifest,signature,public,pin,image,sbom,scan)==digest


def test_binding_check_for_oidc_authenticated_manifest_rejects_changed_artifact(evidence):
    image,sbom,scan,manifest,_,_,_,digest=evidence
    assert release.check_binding(manifest,image,sbom,scan)==digest
    value=json.loads(scan.read_text())
    value['changed']=True
    scan.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='Artifact bytes differ'):
        release.check_binding(manifest,image,sbom,scan)


def test_oidc_manifest_requires_explicit_matching_trust_policy(evidence,tmp_path):
    image,sbom,scan,_,_,_,_,digest=evidence
    manifest=tmp_path/'oidc.json'
    release.create(image,sbom,scan,manifest,signature_policy='github-oidc-slsa-v1')
    with pytest.raises(ValueError,match='Artifact bytes differ'):
        release.check_binding(manifest,image,sbom,scan)
    assert release.check_binding(manifest,image,sbom,scan,signature_policy='github-oidc-slsa-v1')==digest
    with pytest.raises(ValueError,match='Ed25519 signature policy'):
        release.sign(manifest,tmp_path/'nonexistent-key.pem',tmp_path/'unused.sig')


def test_signature_rejects_modified_manifest_bytes(evidence):
    image,sbom,scan,manifest,signature,public,pin,_=evidence
    manifest.write_bytes(manifest.read_bytes()+b' ')
    with pytest.raises(InvalidSignature): release.verify(manifest,signature,public,pin,image,sbom,scan)


def test_signature_rejects_replaced_artifact_even_with_valid_signature(evidence):
    image,sbom,scan,manifest,signature,public,pin,_=evidence
    value=json.loads(scan.read_text())
    value['synthetic_tamper']=True
    scan.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='bytes differ'): release.verify(manifest,signature,public,pin,image,sbom,scan)


def test_signature_requires_out_of_band_pinned_identity(evidence):
    image,sbom,scan,manifest,signature,public,pin,_=evidence
    replacement=Ed25519PrivateKey.generate().public_key()
    public.write_bytes(replacement.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(ValueError,match='not trusted'): release.verify(manifest,signature,public,pin,image,sbom,scan)


@pytest.mark.parametrize('change',['wrong_image','high','no_language'])
def test_release_creation_refuses_unbound_or_failed_scan(evidence,tmp_path,change):
    image,sbom,scan,*_=evidence
    value=json.loads(scan.read_text())
    if change=='wrong_image': value['Metadata']['ImageID']='sha256:'+'0'*64
    elif change=='high': value['Results'][0]['Vulnerabilities']=[{'Severity':'HIGH','VulnerabilityID':'synthetic'}]
    else: value['Results']=value['Results'][:1]
    scan.write_text(json.dumps(value))
    with pytest.raises(ValueError): release.create(image,sbom,scan,tmp_path/'rejected.json')
