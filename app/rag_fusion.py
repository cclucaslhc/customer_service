"""Permission-preserving reciprocal-rank fusion of independent candidate lists."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RankedCandidate:
    tenant_id: str
    document_id: str
    document_version: int
    chunk_id: str
    content_sha256: str
    channel: str
    rank: int


@dataclass(frozen=True)
class FusedCandidate:
    candidate: RankedCandidate
    score: float
    channels: tuple[str, ...]


def reciprocal_rank_fusion(channels: dict[str, list[RankedCandidate]], *,
                           tenant_id: str, k: int = 60, limit: int = 30) -> list[FusedCandidate]:
    """Fuse only already-authorized candidates from one tenant and document version.

    Callers must still query each source with tenant/ACL filters and recheck
    document visibility before returning snippets. This is not an ACL filter.
    """
    if not tenant_id or k <= 0 or limit <= 0:
        raise ValueError('Invalid RRF scope or limits')
    if not channels or not set(channels) <= {'lexical', 'vector'}:
        raise ValueError('Invalid RRF channel')
    scores: dict[tuple[str, str, int, str], float] = {}
    winners: dict[tuple[str, str, int, str], RankedCandidate] = {}
    sources: dict[tuple[str, str, int, str], set[str]] = {}
    for name, items in channels.items():
        seen: set[tuple[str, str, int, str]] = set()
        for expected_rank, item in enumerate(items, 1):
            if (item.tenant_id != tenant_id or item.channel != name or item.rank != expected_rank
                    or not item.document_id or item.document_version <= 0 or not item.chunk_id
                    or len(item.content_sha256) != 64):
                raise ValueError('Invalid or cross-tenant RRF candidate')
            key = (tenant_id, item.document_id, item.document_version, item.content_sha256)
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + expected_rank)
            winners.setdefault(key, item)
            sources.setdefault(key, set()).add(name)
    result = [FusedCandidate(winners[key], score, tuple(sorted(sources[key])))
              for key, score in scores.items()]
    result.sort(key=lambda item: (-item.score, item.candidate.document_id,
                                  item.candidate.document_version, item.candidate.chunk_id))
    if any(not math.isfinite(item.score) for item in result):
        raise ValueError('Invalid RRF score')
    return result[:limit]
