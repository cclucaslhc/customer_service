"""Keep the verification workflow's executable supply chain immutable."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_external_github_actions_are_pinned_to_full_commits():
    workflow = (ROOT / '.github/workflows/verify.yml').read_text(encoding='utf-8')
    actions = [line.split('uses:', 1)[1].strip().split(' #', 1)[0]
               for line in workflow.splitlines() if 'uses:' in line]
    assert actions
    assert all(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}',
                            action) for action in actions)


def test_dockerfile_external_build_sources_are_digest_pinned():
    for path in (ROOT / 'Dockerfile', ROOT / 'Dockerfile.distroless-candidate',ROOT/'ops/grafana/Dockerfile',ROOT/'ops/alertmanager/Dockerfile'):
        dockerfile = path.read_text(encoding='utf-8')
        sources = [line.split(' ', 1)[1].split(' AS ', 1)[0]
                   for line in dockerfile.splitlines() if line.startswith('FROM ')]
        sources.extend(re.findall(r'COPY --from=(\S+)', dockerfile))
        external = [value for value in sources if '/' in value]
        assert external
        assert all(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', value) for value in external)


def test_release_artifact_publication_follows_scan_and_binding_without_keys():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    scan=workflow.index('name: Scan OS and application packages')
    binding=workflow.index('name: Bind OCI, SBOM and passing scan into release evidence')
    publish=workflow.index('name: customer-service-verified-oci-')
    assert scan<binding<publish
    assert '--output /scan/customer-service-vulnerabilities.json' in workflow[scan:binding]
    assert 'ops/release/evidence.py create' in workflow[binding:publish]
    artifact=workflow[publish:]
    assert 'retention-days: 7' in artifact and 'if-no-files-found: error' in artifact
    assert not any(pattern in artifact for pattern in ('*.pem','*.key','/**','secrets.'))


def test_monitoring_scan_precedes_real_dashboard_queries():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    build=workflow.index('name: Build monitoring image with immutable Prometheus plugin')
    scan=workflow.index('name: Scan complete monitoring image before live queries')
    query=workflow.index('name: Verify real private metrics and all Grafana queries')
    assert build<scan<query
    assert '--input /scan/grafana-image.tar' in workflow[scan:query]
    assert '--exit-code 1' in workflow[scan:query]
    assert '--app-image customer-service:verify --grafana-image customer-service-grafana:verify' in workflow[query:]


def test_monitoring_scan_targets_match_actual_acceptance_images():
    import importlib.util
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    for filename,constant in (('verify_grafana','PROMETHEUS'),('verify_collector','COLLECTOR')):
        spec=importlib.util.spec_from_file_location(filename,ROOT/f'scripts/{filename}.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert getattr(module,constant) in workflow
    assert workflow.index('name: Scan pinned Prometheus and Collector images')<workflow.index('name: Verify real Collector privacy and outage recovery')


def test_oidc_attestation_is_scoped_after_all_gates_and_before_final_publication():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    image=workflow[workflow.index('  linux-image:'):]
    assert 'id-token: write' in image and 'attestations: write' in image
    assert 'id-token: write' not in workflow[:workflow.index('  linux-image:')]
    gate=image.index('name: Verify real authenticated Alertmanager receiver without external email')
    attest=image.index('name: Attest bound release evidence using GitHub OIDC')
    publish=image.index('name: customer-service-verified-oci-')
    assert gate<attest<publish
    assert "if: github.event_name == 'push'" in image[attest:publish]
    assert 'subject-path: ${{ runner.temp }}/customer-service-release.json' in image[attest:publish]
    assert '--signature-policy github-oidc-slsa-v1' in image[:attest]


def test_oci_export_uses_two_separate_pinned_container_builders():
    workflow=(ROOT/'.github/workflows/verify.yml').read_text(encoding='utf-8')
    selected=workflow[workflow.index('name: Build and verify byte-reproducible OCI image'):]
    for name in ('cs-oci-ci','cs-oci-rebuild-ci'):
        assert f'buildx create --name {name} --driver docker-container' in selected
        assert f'buildx build --builder {name} ' in selected
    assert selected.count('--driver-opt image=moby/buildkit@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea')==2
