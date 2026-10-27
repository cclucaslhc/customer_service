"""Read-only verification of pinned local Embedding and Reranker weights."""

import argparse
from pathlib import Path

from app.rag_model_manifest import EMBEDDING_WEIGHT, RERANKER_WEIGHT, verify_weight


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embedding-dir', required=True, type=Path)
    parser.add_argument('--reranker-dir', required=True, type=Path)
    args = parser.parse_args()
    for directory, manifest in ((args.embedding_dir, EMBEDDING_WEIGHT),
                                (args.reranker_dir, RERANKER_WEIGHT)):
        verify_weight(directory, manifest)
        print(f'{manifest.repository}@{manifest.revision}: size/SHA-256 verified')


if __name__ == '__main__':
    main()
