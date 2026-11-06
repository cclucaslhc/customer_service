"""Build-only replacement of Debian zlib with fixed, inventoried upstream 1.4."""
import argparse
import ctypes
import gzip
import hashlib
import json
from pathlib import Path
import zlib

VERSION = '1.4'
PURL = 'pkg:generic/zlib/zlib@1.4'
CPE = 'cpe:2.3:a:zlib:zlib:1.4:*:*:*:*:*:*:*'
ARCHIVE_SHA256 = '02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584'
LIBRARY = Path('/usr/lib/x86_64-linux-gnu/libz.so.1.4')
METADATA = Path('/usr/share/customer-service/native/zlib.cdx.json')


def component(binary):
    return {'type':'library','name':'zlib','version':VERSION,'purl':PURL,'bom-ref':PURL,'cpe':CPE,
            'hashes':[{'alg':'SHA-256','content':hashlib.sha256(binary).hexdigest()}],
            'properties':[{'name':'customer-service:source:archive-sha256','value':ARCHIVE_SHA256}],
            'externalReferences':[{'type':'distribution','url':'https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.gz'}]}


def validate_component(value):
    expected=component(b'')
    for key in ('type','name','version','purl','cpe','properties','externalReferences'):
        if value.get(key)!=expected[key]:
            raise ValueError('Pinned upstream zlib provenance required')
    hashes=value.get('hashes',[])
    if (len(hashes)!=1 or hashes[0].get('alg')!='SHA-256'
            or len(hashes[0].get('content',''))!=64
            or any(c not in '0123456789abcdef' for c in hashes[0].get('content',''))):
        raise ValueError('Actual zlib binary hash required')
    return value


def prepare_root(root, library):
    # Only mutates a new Docker build staging root. It never edits the live host.
    if not root.is_absolute() or root.is_symlink() or root.name!='runtime-root':
        raise ValueError('Explicit build-only runtime-root required')
    root=root.resolve(strict=True)
    directory=root/'usr/lib/x86_64-linux-gnu'
    status=root/'var/lib/dpkg/status.d/zlib1g'
    old=directory/'libz.so.1.3.1'
    link=directory/'libz.so.1'
    md5=root/'var/lib/dpkg/status.d/zlib1g.md5sums'
    targets=(directory,status.parent,old,status,md5)
    if any(not x.resolve(strict=True).is_relative_to(root) for x in targets):
        raise ValueError('Native replacement escapes build staging root')
    if (not old.is_file() or old.is_symlink() or not link.is_symlink()
            or link.readlink()!=Path('libz.so.1.3.1')
            or 'Package: zlib1g\n' not in status.read_text()):
        raise ValueError('Unexpected base zlib package/layout; manual review required')
    binary=library.read_bytes()
    if not binary.startswith(b'\x7fELF'):
        raise ValueError('Actual ELF replacement library required')
    # These are exactly the superseded package's two runtime files and two
    # provenance files. No wildcard deletion or phantom Debian version record.
    for path in (link,old,status,md5):
        path.unlink()
    target=root/LIBRARY.as_posix().lstrip('/')
    target.write_bytes(binary)
    target.chmod(0o755)
    link.symlink_to(target.name)
    metadata=root/METADATA.as_posix().lstrip('/')
    metadata.parent.mkdir(parents=True,exist_ok=True)
    metadata.write_text(json.dumps({'bomFormat':'CycloneDX','specVersion':'1.6','version':1,
                                   'components':[component(binary)]},sort_keys=True),encoding='utf-8')


def enrich(sbom,native):
    document=json.loads(sbom.read_text())
    values=json.loads(native.read_text()).get('components',[])
    if len(values)!=1:
        raise ValueError('Exactly one zlib component required')
    value=validate_component(values[0])
    components=document.get('components',[])
    if any(x.get('purl','').startswith('pkg:deb/debian/zlib1g@') for x in components):
        raise ValueError('Superseded Debian zlib still present')
    existing=[x for x in components if x.get('purl')==PURL]
    if existing:
        if len(existing)!=1 or existing[0].get('version')!=VERSION or existing[0].get('hashes')!=value['hashes']:
            raise ValueError('Conflicting zlib binary identity')
        components[components.index(existing[0])]=value
    else:
        components.append(value)
    document['components']=components
    sbom.write_text(json.dumps(document,sort_keys=True),encoding='utf-8')


def probe():
    if json.loads(METADATA.read_text()).get('components')!=[component(LIBRARY.read_bytes())]:
        raise ValueError('Actual zlib binary differs from inventory')
    lib=ctypes.CDLL(str(LIBRARY))
    lib.zlibVersion.restype=ctypes.c_char_p
    if lib.zlibVersion()!=b'1.4' or zlib.ZLIB_RUNTIME_VERSION!=VERSION:
        raise ValueError('Python is not loading fixed upstream zlib')
    if Path('/usr/lib/x86_64-linux-gnu/libz.so.1.3.1').exists() or Path('/var/lib/dpkg/status.d/zlib1g').exists():
        raise ValueError('Superseded zlib still present')
    data=b'Synthetic zlib compatibility probe\x00'*4096
    if gzip.decompress(gzip.compress(data,mtime=0))!=data or zlib.decompress(zlib.compress(data))!=data:
        raise ValueError('zlib/gzip compatibility failed')
    print('Actual upstream zlib 1.4 identity/Python loading/gzip/deflate verified')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    root=sub.add_parser('prepare-root')
    root.add_argument('root',type=Path)
    root.add_argument('library',type=Path)
    add=sub.add_parser('enrich')
    add.add_argument('sbom',type=Path)
    add.add_argument('native',type=Path)
    sub.add_parser('probe')
    args=parser.parse_args()
    if args.action=='prepare-root': prepare_root(args.root,args.library)
    elif args.action=='enrich': enrich(args.sbom,args.native)
    else: probe()


if __name__=='__main__':
    main()
