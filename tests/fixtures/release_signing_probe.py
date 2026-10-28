"""Sign actual local release artifacts using a NEW disposable test identity only."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT=Path(__file__).resolve().parents[2]


def main(oci,sbom,scan):
    spec=importlib.util.spec_from_file_location('release_evidence',ROOT/'ops/release/evidence.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output=Path(tempfile.mkdtemp(prefix='cs-s2-release-signing-'))
    output.chmod(0o700)
    key=Ed25519PrivateKey.generate()
    private,public=output/'synthetic-signing-private.pem',output/'synthetic-signing-public.pem'
    with private.open('xb') as target:
        private.chmod(0o600)
        target.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    manifest,signature=output/'release.json',output/'release.sig.json'
    subject=module.create(oci,sbom,scan,manifest)
    pin=module.sign(manifest,private,signature)
    assert module.verify(manifest,signature,public,pin,oci,sbom,scan)==subject
    changed=output/'changed-manifest.json'
    changed.write_bytes(manifest.read_bytes()+b' ')
    from cryptography.exceptions import InvalidSignature
    try: module.verify(changed,signature,public,pin,oci,sbom,scan)
    except InvalidSignature: pass
    else: raise AssertionError('Tampered manifest accepted')
    private.unlink() # Exact newly generated disposable test key, not an operator identity.
    print(json.dumps({'oci_manifest':subject,'signature_verified':True,'tamper_rejected':True,
        'test_public_key_sha256':pin,'evidence_directory':str(output),
        'identity_scope':'new disposable test identity; NOT a production release signature'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('oci','sbom','scan'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    main(args.oci,args.sbom,args.scan)
