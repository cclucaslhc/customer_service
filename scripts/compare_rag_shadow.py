"""Offline owner-only RAG shadow comparison; never changes live routing."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.persistence.engine import build_engine
from app.persistence.schema import SCHEMA_REVISION
from app.rag_index_admin import RagIndexAdmin
from app.rag_local import LocalEmbeddingGateway
from app.runtime_files import worker_database_url


def load_questions(path: Path) -> list[str]:
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError('Questions file must be an existing bounded absolute path')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list) or not 1 <= len(data) <= 100 or any(
            not isinstance(value, str) or not 1 <= len(value) <= 1000 for value in data):
        raise ValueError('Questions must be a JSON array of 1-100 bounded strings')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database-url', help='Loopback test URL only; visible in process arguments')
    source.add_argument('--database-url-file', type=Path, help='Absolute private migration-owner URL file')
    parser.add_argument('--tenant-id', required=True)
    parser.add_argument('--ready-index-id', required=True)
    parser.add_argument('--questions-file', type=Path, required=True,
                        help='Controlled local JSON array; question text never appears in output')
    parser.add_argument('--model-dir', type=Path, required=True,
                        help='Verified local Embedding weight directory')
    parser.add_argument('--max-tokens', type=int, required=True)
    parser.add_argument('--top-k', type=int, default=3)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    args = parser.parse_args()
    questions = load_questions(args.questions_file)
    gateway = LocalEmbeddingGateway(args.model_dir, device=args.device,
                                    max_length=args.max_tokens)
    vectors = []
    for offset in range(0, len(questions), 16):
        vectors.extend(gateway.encode(questions[offset:offset + 16], query=True))
    engine = build_engine(worker_database_url(args.database_url, args.database_url_file), pool_size=1)
    try:
        with engine.connect() as db:
            if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() != SCHEMA_REVISION:
                raise RuntimeError('Shadow comparison requires exact schema head')
        report = RagIndexAdmin(engine).shadow_compare(tenant_id=args.tenant_id,
            index_id=args.ready_index_id, vectors=vectors, top_k=args.top_k,
            max_tokens=args.max_tokens)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('RAG shadow comparison failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
