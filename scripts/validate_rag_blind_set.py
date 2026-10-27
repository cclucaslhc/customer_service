"""Validate a private JSONL blind set without printing questions or sources."""

import argparse
import json

from app.rag_blind_set import validate_blind_set


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('jsonl', help='Path to approved private JSONL outside the repository')
    args = parser.parse_args()
    print(json.dumps(validate_blind_set(args.jsonl), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
