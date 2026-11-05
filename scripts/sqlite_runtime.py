"""Pinned upstream SQLite provenance and native scan coverage (no VEX/ignores)."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import ctypes
import shutil

VERSION = '3.54.0'
ARCHIVE_SHA256 = '134ec0802dda5795816e25d25872d20b312cb3973438c49b30bc40b7705ea9ed'
SOURCE_SHA3 = '8efd453d08a7cfc4c79de5923153036cdc4dcaa254d4f1e617be6a2a98828f9e'
SOURCE_ID = '2026-10-09 15:46:58 be8d059e9a49089ab2dce5ed26dd87aaf598fdc5fbe0b107c0dd758464bcd5e3'
PURL = 'pkg:generic/sqlite/sqlite@' + VERSION
CPE = 'cpe:2.3:a:sqlite:sqlite:' + VERSION + ':*:*:*:*:*:*:*'
LIBRARY = Path('/usr/local/lib/libsqlite3.so.0')
METADATA = Path('/usr/share/customer-service/native/sqlite3.cdx.json')
VEX_PATH = Path(__file__).resolve().parents[1] / 'ops/release/sqlite-library.openvex.json'


def component(binary):
    return {'type': 'library', 'name': 'sqlite', 'version': VERSION, 'purl': PURL,
            'bom-ref': PURL, 'cpe': CPE,
            'hashes': [{'alg': 'SHA-256', 'content': hashlib.sha256(binary).hexdigest()}],
            'properties': [{'name': 'customer-service:source:archive-sha256', 'value': ARCHIVE_SHA256},
                           {'name': 'customer-service:source:sqlite3.c-sha3-256', 'value': SOURCE_SHA3},
                           {'name': 'customer-service:build:sqlite-mode', 'value': 'library-only-amalgamation-v1'}],
            'externalReferences': [{'type': 'distribution', 'url': 'https://sqlite.org/2026/sqlite-autoconf-3540000.tar.gz'}]}


def validate_component(value):
    hashes = value.get('hashes', [])
    if (value.get('name') != 'sqlite' or value.get('version') != VERSION
            or value.get('purl') != PURL or value.get('cpe') != CPE
            or len(hashes) != 1 or hashes[0].get('alg') != 'SHA-256'
            or len(hashes[0].get('content', '')) != 64
            or any(c not in '0123456789abcdef' for c in hashes[0].get('content', ''))):
        raise ValueError('Pinned upstream SQLite inventory required')
    expected = component(b'')['properties']
    if value.get('properties') != expected:
        raise ValueError('SQLite source provenance missing')
    return value


def probe():
    inventory = json.loads(METADATA.read_text())
    actual = component(LIBRARY.read_bytes())
    if inventory.get('components') != [actual] or sqlite3.sqlite_version != VERSION:
        raise ValueError('SQLite loaded version or actual library differs')
    library = ctypes.CDLL(str(LIBRARY))
    if shutil.which('sqlite3') is not None or any(hasattr(library, name) for name in (
            'sqlite3_recover_init', 'sqlite3_recover_run', 'sqlite3_shell_init')):
        raise ValueError('SQLite CLI/recovery code unexpectedly present')
    with sqlite3.connect(':memory:') as db:
        if db.execute('select sqlite_source_id()').fetchone()[0] != SOURCE_ID:
            raise ValueError('SQLite source ID differs')
        # Upstream e1c9934 regression, exercising the formerly overflowing merge.
        for detail in ('none', 'column', 'full'):
            db.execute(f'CREATE VIRTUAL TABLE t_{detail} USING fts5(x, detail={detail})')
            db.executemany(f'INSERT INTO t_{detail}(rowid,x) VALUES(?,?)',
                           [(i, 'ab') for i in range(1, 56)] + [(-9223372036854775808, 'abc')])
            if db.execute(f"SELECT count(*) FROM t_{detail}('a*')").fetchone()[0] != 56:
                raise ValueError('SQLite FTS5 regression failed')
    print('SQLite actual library/source/absent CLI/recovery/FTS5 regression verified: ' + VERSION)


def enrich(sbom, native):
    document = json.loads(sbom.read_text())
    values = json.loads(native.read_text()).get('components', [])
    if len(values) != 1:
        raise ValueError('One upstream SQLite component required')
    value = validate_component(values[0])
    components = document.get('components', [])
    # Never hide a still-present Debian library or silently replace its version.
    if any(x.get('purl', '').startswith('pkg:deb/debian/libsqlite3-0@') for x in components):
        raise ValueError('Old Debian SQLite still inventoried')
    existing = [x for x in components if x.get('purl') == PURL]
    if existing:
        # Trivy may import the embedded CDX while normalizing fields (notably
        # CPE/source properties). Retain its binary identity check, then restore
        # complete upstream provenance for the supplemental native scanner.
        if (len(existing) != 1 or existing[0].get('version') != VERSION
                or existing[0].get('hashes') != value['hashes']):
            raise ValueError('Conflicting native SQLite inventory')
        components[components.index(existing[0])] = value
    else:
        components.append(value)
    document['components'] = components
    sbom.write_text(json.dumps(document, sort_keys=True), encoding='utf-8')


def bind_scan(scan, native_scan, sbom):
    report = json.loads(scan.read_text())
    native = json.loads(native_scan.read_text())
    vex = json.loads(VEX_PATH.read_text())
    validate_native_report(native, json.loads(sbom.read_text()), vex)
    report['CustomerServiceNativeScan'] = {'sbom_sha256': hashlib.sha256(sbom.read_bytes()).hexdigest(),
                                          'report': native, 'vex': vex}
    scan.write_text(json.dumps(report, sort_keys=True), encoding='utf-8')


def validate_native_report(native, inventory=None, vex=None):
    descriptor = native.get('descriptor', {})
    source = native.get('source', {})
    source_valid = source.get('type') == 'sbom-file'
    # Grype reconstructs image source metadata from a real container CDX. Its
    # generated imageID is not an OCI digest; bind the original name to the
    # exact SBOM, whose Trivy ImageID and bytes are checked by release evidence.
    if source.get('type') == 'image' and inventory is not None:
        root = inventory.get('metadata', {}).get('component', {})
        source_valid = (root.get('type') == 'container' and bool(root.get('name'))
                        and source.get('target', {}).get('userInput') == root['name'])
    if (descriptor.get('name') != 'grype' or descriptor.get('version') != '0.121.0'
            or descriptor.get('db', {}).get('status', {}).get('valid') is not True
            or not source_valid
            or not isinstance(native.get('matches'), list)):
        raise ValueError('Actual Grype native scan with valid database required')
    if any(x.get('vulnerability', {}).get('severity', '').upper() in {'HIGH', 'CRITICAL'}
           for x in native['matches']):
        raise ValueError('Unresolved native vulnerabilities')
    if vex is not None and vex != json.loads(VEX_PATH.read_text()):
        raise ValueError('Unapproved native VEX policy')
    ignored = native.get('ignoredMatches') or []
    if ignored:
        components = (inventory or {}).get('components', [])
        sqlite = [x for x in components if x.get('purl') == PURL]
        if vex is None or len(sqlite) != 1 or len(ignored) != 1:
            raise ValueError('Native vulnerability exclusions forbidden')
        validate_component(sqlite[0])
        item = ignored[0]
        artifact = item.get('artifact', {})
        vulnerability = item.get('vulnerability', {})
        if (vulnerability.get('id') != 'CVE-2026-119586' or vulnerability.get('namespace') != 'nvd:cpe'
                or artifact.get('purl') != PURL or artifact.get('version') != VERSION
                or item.get('appliedIgnoreRules') != [{'namespace': 'vex', 'vex-status': 'not_affected'}]):
            raise ValueError('Only absent SQLite CLI code has a reviewed not-affected determination')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('probe')
    control = sub.add_parser('scanner-control')
    control.add_argument('output', type=Path)
    check_control = sub.add_parser('check-scanner-control')
    check_control.add_argument('report', type=Path)
    build = sub.add_parser('metadata')
    build.add_argument('library', type=Path)
    build.add_argument('output', type=Path)
    add = sub.add_parser('enrich')
    add.add_argument('sbom', type=Path)
    add.add_argument('native', type=Path)
    bind = sub.add_parser('bind-scan')
    bind.add_argument('scan', type=Path)
    bind.add_argument('native_scan', type=Path)
    bind.add_argument('sbom', type=Path)
    args = parser.parse_args()
    if args.action == 'probe':
        probe()
    elif args.action == 'scanner-control':
        args.output.write_text(json.dumps({'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1,
            'components': [{'type': 'library', 'name': 'sqlite', 'version': '3.46.1',
                            'purl': 'pkg:generic/sqlite/sqlite@3.46.1',
                            'cpe': 'cpe:2.3:a:sqlite:sqlite:3.46.1:*:*:*:*:*:*:*',
                            'bom-ref': 'sqlite-known-vulnerable-control'}]}), encoding='utf-8')
    elif args.action == 'check-scanner-control':
        report = json.loads(args.report.read_text())
        if not any(x.get('vulnerability', {}).get('id') == 'CVE-2025-3277'
                   and x.get('vulnerability', {}).get('severity', '').upper() == 'CRITICAL'
                   for x in report.get('matches', [])):
            raise ValueError('Native scanner failed known-vulnerable SQLite control')
    elif args.action == 'metadata':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'bomFormat': 'CycloneDX', 'specVersion': '1.6',
                                          'version': 1, 'components': [component(args.library.read_bytes())]},
                                         sort_keys=True), encoding='utf-8')
    elif args.action == 'enrich':
        enrich(args.sbom, args.native)
    else:
        bind_scan(args.scan, args.native_scan, args.sbom)


if __name__ == '__main__':
    main()
