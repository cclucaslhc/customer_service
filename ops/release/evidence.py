"""Offline Ed25519 release evidence; explicit files and externally pinned key.

No key generation, environment credential discovery, upload or trust-on-first-use.
Signatures bind OCI/SBOM/scan bytes; production key custody is an operator task.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_image_rebuild import image_digest
from verify_sbom import verify_sbom
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey


def bounded(path,maximum):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>maximum:
        raise ValueError('Explicit bounded regular file required')
    return path.read_bytes()


def digest_file(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Explicit regular artifact required')
    digest=hashlib.sha256()
    size=0
    with path.open('rb') as source:
        for block in iter(lambda:source.read(8*1024*1024),b''):
            digest.update(block)
            size+=len(block)
    return {'sha256':digest.hexdigest(),'bytes':size}


def build_evidence(oci,sbom,scan):
    subject=image_digest(Path(oci)) # Verify every OCI descriptor's actual bytes.
    with tarfile.open(oci,'r:*') as archive:
        manifest=json.load(archive.extractfile('blobs/sha256/'+subject[7:]))
    config=manifest['config']['digest']
    bounded(sbom,32*1024*1024)
    verify_sbom(Path(sbom))
    inventory=json.loads(bounded(sbom,32*1024*1024))
    properties=inventory.get('metadata',{}).get('component',{}).get('properties',[])
    if not any(item.get('name')=='aquasecurity:trivy:ImageID' and item.get('value')==config for item in properties):
        raise ValueError('SBOM is not bound to this image config')
    report=json.loads(bounded(scan,32*1024*1024))
    if report.get('SchemaVersion')!=2 or report.get('ArtifactType')!='container_image' or report.get('Metadata',{}).get('ImageID')!=config:
        raise ValueError('Scan report is not bound to this image config')
    results=report.get('Results',[])
    if not {'os-pkgs','lang-pkgs'}<={item.get('Class') for item in results}:
        raise ValueError('Both OS and language scan coverage required')
    if any(v.get('Severity') in ('HIGH','CRITICAL') for item in results for v in (item.get('Vulnerabilities') or [])):
        raise ValueError('Unresolved high or critical vulnerabilities')
    return {'format':'customer-service-release-evidence-v1','oci_manifest':subject,'oci_config':config,
        'artifacts':{'oci':digest_file(oci),'sbom':digest_file(sbom),'scan':digest_file(scan)},
        'scan_gate':'HIGH_CRITICAL_ZERO','signature_policy':'explicit-pinned-ed25519-v1'}


def create(oci,sbom,scan,output):
    evidence=build_evidence(oci,sbom,scan)
    with Path(output).open('xb') as destination:
        destination.write(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode())
    return evidence['oci_manifest']


def fingerprint(key):
    return hashlib.sha256(key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def private_key(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink(): raise ValueError('Explicit private signing key file required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>16384: raise ValueError('Invalid signing key file')
        if os.name=='posix' and stat.S_IMODE(info.st_mode)&0o077: raise ValueError('Signing key must be private')
        key=serialization.load_pem_private_key(source.read(16385),password=None)
    if not isinstance(key,Ed25519PrivateKey): raise ValueError('Ed25519 signing key required')
    return key


def sign(manifest,key_file,signature_file):
    payload=bounded(manifest,16384)
    document=json.loads(payload)
    if document.get('format')!='customer-service-release-evidence-v1': raise ValueError('Unknown evidence format')
    key=private_key(key_file)
    envelope={'format':'customer-service-ed25519-signature-v1','key_sha256':fingerprint(key.public_key()),
        'signature':base64.b64encode(key.sign(payload)).decode()}
    with Path(signature_file).open('x',encoding='utf-8') as output:
        json.dump(envelope,output,sort_keys=True)
    return envelope['key_sha256']


def check_binding(manifest,oci,sbom,scan):
    """Byte binding only. Caller must separately authenticate the manifest."""
    expected=build_evidence(oci,sbom,scan)
    if json.loads(bounded(manifest,16384))!=expected:
        raise ValueError('Artifact bytes differ from release evidence')
    return expected['oci_manifest']


def verify(manifest,signature_file,public_key_file,trusted_key_sha256,oci,sbom,scan):
    if not re.fullmatch('[0-9a-f]{64}',trusted_key_sha256): raise ValueError('Pinned public-key fingerprint required')
    key=serialization.load_pem_public_key(bounded(public_key_file,16384))
    if not isinstance(key,Ed25519PublicKey) or fingerprint(key)!=trusted_key_sha256:
        raise ValueError('Release signing identity is not trusted')
    envelope=json.loads(bounded(signature_file,16384))
    if envelope.get('format')!='customer-service-ed25519-signature-v1' or envelope.get('key_sha256')!=trusted_key_sha256:
        raise ValueError('Signature identity mismatch')
    payload=bounded(manifest,16384)
    key.verify(base64.b64decode(envelope['signature'],validate=True),payload)
    expected=build_evidence(oci,sbom,scan)
    if json.loads(payload)!=expected: raise ValueError('Artifact bytes differ from signed evidence')
    return expected['oci_manifest']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    for action in ('create','verify','check-binding'):
        child=commands.add_parser(action)
        for name in ('oci','sbom','scan','manifest'):
            child.add_argument('--'+name,type=Path,required=True)
        if action=='verify':
            child.add_argument('--signature',type=Path,required=True)
            child.add_argument('--public-key',type=Path,required=True)
            child.add_argument('--trusted-key-sha256',required=True)
    child=commands.add_parser('sign')
    for name in ('manifest','key-file','signature'): child.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.action=='create': value=create(args.oci,args.sbom,args.scan,args.manifest)
    elif args.action=='sign': value=sign(args.manifest,args.key_file,args.signature)
    elif args.action=='check-binding': value=check_binding(args.manifest,args.oci,args.sbom,args.scan)
    else: value=verify(args.manifest,args.signature,args.public_key,args.trusted_key_sha256,args.oci,args.sbom,args.scan)
    print(args.action+' verified: '+value)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('Release evidence failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
