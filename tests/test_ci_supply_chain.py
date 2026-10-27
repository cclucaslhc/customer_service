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
    for path in (ROOT / 'Dockerfile', ROOT / 'Dockerfile.distroless-candidate'):
        dockerfile = path.read_text(encoding='utf-8')
        sources = [line.split(' ', 1)[1].split(' AS ', 1)[0]
                   for line in dockerfile.splitlines() if line.startswith('FROM ')]
        sources.extend(re.findall(r'COPY --from=(\S+)', dockerfile))
        external = [value for value in sources if '/' in value]
        assert external
        assert all(re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', value) for value in external)
