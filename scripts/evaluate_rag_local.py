"""Offline development-set ablation; never uses live documents or model APIs."""

import argparse
import json
import tempfile
import time
from hashlib import sha256
from pathlib import Path

from app.knowledge import KnowledgeService
from app.rag_fusion import RankedCandidate, reciprocal_rank_fusion
from app.rag_local import LocalEmbeddingGateway, LocalReranker
from scripts.evaluate import FIXTURES, configure_isolated_storage, percentile


def ranked(names: list[str], channel: str, contents: dict[str, str]) -> list[RankedCandidate]:
    return [RankedCandidate('synthetic-tenant', name, 1, name,
                            sha256(contents[name].encode('utf-8')).hexdigest(), channel, index)
            for index, name in enumerate(names, 1)]


def metrics(cases: list[dict]) -> dict:
    if not cases:
        return {'count': 0}
    return {'count': len(cases),
            'recall_at_1': sum(item['rank'] == 1 for item in cases) / len(cases),
            'recall_at_3': sum(item['rank'] is not None and item['rank'] <= 3 for item in cases) / len(cases)}


def evaluate(root: Path, *, device: str) -> dict:
    corpus = json.loads((FIXTURES / 'knowledge_retrieval_eval.json').read_text(encoding='utf-8'))
    contents = {item['filename']: item['content'] for item in corpus['documents']}
    cases = [*corpus['queries'], *corpus['challenge_queries']]
    negatives = [*({'query': value, 'category': 'easy'} for value in corpus['negative_queries']),
                 *corpus['hard_negative_queries']]
    with tempfile.TemporaryDirectory(prefix='cs-rag-ablation-') as temp_dir:
        configure_isolated_storage(Path(temp_dir))
        service = KnowledgeService()
        for item in corpus['documents']:
            service.ingest(item['filename'], item['content'].encode('utf-8'))
        embedder = LocalEmbeddingGateway(root / 'embedding', device=device, max_length=256)
        names = list(contents)
        vectors = embedder.encode([contents[name] for name in names])
        reranker = LocalReranker(root / 'reranker', device=device, max_length=512)
        outcomes = []
        latencies = []
        for case in [*cases, *negatives]:
            started = time.perf_counter()
            query = case['query']
            query_vector = embedder.encode([query], query=True)[0]
            lexical = list(dict.fromkeys(item['display_name'] for item in service.search(query, 15)))
            similarity = [sum(a * b for a, b in zip(query_vector, vector)) for vector in vectors]
            vector_names = [names[index] for index in sorted(range(len(names)), key=lambda i: -similarity[i])]
            fused = reciprocal_rank_fusion({
                'lexical': ranked(lexical, 'lexical', contents),
                'vector': ranked(vector_names, 'vector', contents),
            }, tenant_id='synthetic-tenant', limit=8)
            fused_names = [item.candidate.document_id for item in fused]
            scores = reranker.score(query, [contents[name] for name in fused_names])
            reranked = [name for name, _ in sorted(zip(fused_names, scores), key=lambda item: -item[1])]
            score_by_name = dict(zip(fused_names, scores))
            latencies.append((time.perf_counter() - started) * 1000)
            outcome = {'query': query, 'category': case.get('category', 'smoke'),
                       'expected': case.get('expected'), 'top_score': max(scores) if scores else None}
            for channel, selected in (('lexical', lexical), ('vector', vector_names),
                                      ('fusion', fused_names), ('reranked', reranked)):
                expected = case.get('expected')
                outcome[channel] = {'top3': selected[:3],
                                    'rank': selected.index(expected) + 1 if expected in selected else None}
            outcomes.append(outcome)

    positives = outcomes[:len(cases)]
    hard = [item for item in outcomes[len(cases):] if item['category'] != 'easy']
    return {
        'dataset': corpus['dataset'],
        'scope': 'Synthetic development set; exhaustive in-memory vectors, not pgvector/ANN or a blind test',
        'models': {'embedding': 'Qwen3-Embedding-0.6B pinned', 'reranker': 'Qwen3-Reranker-0.6B pinned'},
        'device': device,
        'positive': {name: metrics([{'rank': item[name]['rank']} for item in positives])
                     for name in ('lexical', 'vector', 'fusion', 'reranked')},
        'colloquial': {name: metrics([{'rank': item[name]['rank']} for item in positives
                                     if item['category'] == 'colloquial'])
                      for name in ('lexical', 'vector', 'fusion', 'reranked')},
        'hard_negative_gate_exploration': {
            str(threshold): {'rejection_rate': sum(
                item['top_score'] is None or item['top_score'] < threshold for item in hard) / len(hard),
                'positive_false_rejection_rate': sum(
                    item['top_score'] is None or item['top_score'] < threshold for item in positives) / len(positives)}
            for threshold in (0.5, 0.8, 0.9, 0.95)},
        'retrieval_and_rerank_latency_ms': {'p50': percentile(latencies, 0.5),
                                            'p95': percentile(latencies, 0.95)},
        'failed_positive_queries': [item for item in positives if item['reranked']['rank'] is None or
                                    item['reranked']['rank'] > 3],
        'hard_negative_scores': [{'query': item['query'], 'score': item['top_score'],
                                  'top3': item['reranked']['top3']} for item in hard],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-root', required=True, type=Path)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    args = parser.parse_args()
    print(json.dumps(evaluate(args.model_root, device=args.device), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
