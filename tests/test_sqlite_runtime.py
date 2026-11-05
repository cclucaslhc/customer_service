"""Native provenance must remain visible after replacing the vulnerable library."""
import json
from pathlib import Path
import pytest
from scripts.sqlite_runtime import component, validate_component, enrich, bind_scan, validate_native_report, VEX_PATH, PURL


def test_component_pins_upstream_source_and_actual_binary():
    value = component(b'new binary')
    assert validate_component(value) == value
    assert value['hashes'] != component(b'old binary')['hashes']
    value['version'] = '3.46.1'
    with pytest.raises(ValueError):
        validate_component(value)


def test_enrichment_refuses_old_debian_library_and_conflicts(tmp_path):
    sbom, native = tmp_path/'sbom.json', tmp_path/'native.json'
    native.write_text(json.dumps({'components': [component(b'new binary')]}))
    sbom.write_text(json.dumps({'components': [{'purl':'pkg:deb/debian/libsqlite3-0@3.46.1'}]}))
    with pytest.raises(ValueError, match='Old Debian'):
        enrich(sbom, native)
    sbom.write_text(json.dumps({'components': []}))
    enrich(sbom, native)
    enrich(sbom, native)
    assert json.loads(sbom.read_text())['components'] == [component(b'new binary')]
    normalized = component(b'new binary')
    del normalized['cpe']
    del normalized['properties']
    sbom.write_text(json.dumps({'components':[normalized]}))
    enrich(sbom, native)
    assert json.loads(sbom.read_text())['components'] == [component(b'new binary')]
    bad = component(b'another binary')
    sbom.write_text(json.dumps({'components': [bad]}))
    with pytest.raises(ValueError, match='Conflicting'):
        enrich(sbom, native)


@pytest.mark.parametrize('mode', ['high', 'critical', 'ignored', 'missing', 'invalid_db', 'wrong_source', 'wrong_version'])
def test_native_scan_cannot_be_omitted_or_suppressed(tmp_path, mode):
    scan, native, sbom = [tmp_path/x for x in ('scan.json','native.json','sbom.json')]
    scan.write_text('{}')
    sbom.write_text('{}')
    report = {'descriptor': {'name':'grype', 'version':'0.121.0','db':{'status':{'valid':True}}},
              'source':{'type':'sbom-file'}, 'matches': []}
    if mode in ('high', 'critical'):
        report['matches'] = [{'vulnerability': {'severity': mode.title()}}]
    elif mode == 'ignored':
        report['ignoredMatches'] = [{'synthetic':True}]
    elif mode == 'missing':
        del report['matches']
    elif mode == 'invalid_db':
        report['descriptor']['db']['status']['valid'] = False
    elif mode == 'wrong_version':
        report['descriptor']['version'] = 'synthetic'
    else:
        report['source']['type'] = 'directory'
    native.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        bind_scan(scan, native, sbom)


def test_native_report_with_valid_database_and_zero_findings():
    validate_native_report({'descriptor':{'name':'grype','version':'0.121.0','db':{'status':{'valid':True}}},
                            'source':{'type':'sbom-file'},'matches':[]})


def test_real_container_sbom_source_is_bound_to_inventory_name():
    report={'descriptor':{'name':'grype','version':'0.121.0','db':{'status':{'valid':True}}},
            'source':{'type':'image','target':{'userInput':'/scan/actual-oci'}},'matches':[]}
    inventory={'metadata':{'component':{'type':'container','name':'/scan/actual-oci'}}}
    validate_native_report(report,inventory)
    with pytest.raises(ValueError):
        validate_native_report(report)
    report['source']['target']['userInput']='/scan/other-oci'
    with pytest.raises(ValueError):
        validate_native_report(report,inventory)


def library_only_report():
    return {'descriptor':{'name':'grype','version':'0.121.0','db':{'status':{'valid':True}}},
            'source':{'type':'sbom-file'},'matches':[],
            'ignoredMatches':[{'vulnerability':{'id':'CVE-2026-119586','namespace':'nvd:cpe'},
                              'artifact':{'purl':PURL,'version':'3.54.0'},
                              'appliedIgnoreRules':[{'namespace':'vex','vex-status':'not_affected'}]}]}


def test_cli_only_finding_requires_exact_reviewed_vex_and_native_provenance():
    report = library_only_report()
    inventory = {'components':[component(b'synthetic shared library')]}
    vex = json.loads(VEX_PATH.read_text())
    validate_native_report(report,inventory,vex)
    with pytest.raises(ValueError):
        validate_native_report(report,inventory)
    vex['statements'][0]['status']='fixed'
    with pytest.raises(ValueError):
        validate_native_report(report,inventory,vex)


@pytest.mark.parametrize('change',['other_cve','other_package','other_rule','extra_finding','no_provenance'])
def test_cli_determination_cannot_exclude_other_vulnerabilities(change):
    report=library_only_report()
    inventory={'components':[component(b'synthetic library')]}
    item=report['ignoredMatches'][0]
    if change=='other_cve': item['vulnerability']['id']='CVE-2026-119373'
    elif change=='other_package': item['artifact']['purl']='pkg:generic/other@1'
    elif change=='other_rule': item['appliedIgnoreRules']=[{'namespace':'manual-ignore'}]
    elif change=='extra_finding': report['ignoredMatches'].append(item.copy())
    else: inventory['components'][0]['properties']=[]
    with pytest.raises(ValueError):
        validate_native_report(report,inventory,json.loads(VEX_PATH.read_text()))


def test_native_build_and_ci_keep_remediation_and_coverage():
    root = Path(__file__).resolve().parents[1]
    build = (root/'Dockerfile').read_text()
    assert 'sqlite-autoconf-3540000.tar.gz' in build
    assert 'openssl dgst -sha3-256 sqlite3.c' in build
    assert '-DSQLITE_ENABLE_FTS5' in build
    assert '/lib/x86_64-linux-gnu/libsqlite3.so.0 /lib/' not in build
    workflow = (root/'.github/workflows/verify.yml').read_text()
    assert 'scripts/sqlite_runtime.py probe' in workflow
    assert 'scripts/sqlite_runtime.py enrich' in workflow
    assert 'scripts/sqlite_runtime.py bind-scan' in workflow
    assert '--fail-on high -o json' in workflow
    assert '--vex "$GITHUB_WORKSPACE/ops/release/sqlite-library.openvex.json"' in workflow
