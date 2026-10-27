"""Offline synthetic GPU/CPU smoke check for the pinned local RAG models."""

import argparse
import json
import time
from pathlib import Path

from app.rag_model_manifest import EMBEDDING_WEIGHT, RERANKER_WEIGHT, verify_weight


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embedding-dir', required=True, type=Path)
    parser.add_argument('--reranker-dir', required=True, type=Path)
    args = parser.parse_args()
    verify_weight(args.embedding_dir, EMBEDDING_WEIGHT)
    verify_weight(args.reranker_dir, RERANKER_WEIGHT)

    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    query = 'Instruct: Given a customer support question, retrieve passages that answer it.\nQuery:退货需要什么资料？'
    passages = ['退货授权需要订单号和产品批次。', '产品停产通知应写明最后订购日期。']
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.embedding_dir, local_files_only=True,
                                              trust_remote_code=False, padding_side='left')
    model = AutoModel.from_pretrained(args.embedding_dir, local_files_only=True,
                                      trust_remote_code=False, dtype=dtype).to(device).eval()
    inputs = tokenizer([query, *passages], padding=True, truncation=True,
                       max_length=256, return_tensors='pt').to(device)
    with torch.inference_mode():
        outputs = model(**inputs)
        embeddings = F.normalize(outputs.last_hidden_state[:, -1, :].float(), p=2, dim=1)
        similarities = (embeddings[0:1] @ embeddings[1:].T).squeeze(0).tolist()
    embedding_seconds = time.perf_counter() - started
    embedding_shape = list(embeddings.shape)
    del model, outputs, embeddings, inputs
    if device == 'cuda':
        torch.cuda.empty_cache()

    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.reranker_dir, local_files_only=True,
                                              trust_remote_code=False, padding_side='left')
    model = AutoModelForCausalLM.from_pretrained(args.reranker_dir, local_files_only=True,
                                                 trust_remote_code=False, dtype=dtype).to(device).eval()
    prefix = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
              'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
              '<|im_start|>user\n')
    suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    task = 'Given a customer support question, retrieve passages that answer it'
    prompts = [prefix + f'<Instruct>: {task}\n<Query>:退货需要什么资料？\n<Document>:{passage}' + suffix
               for passage in passages]
    inputs = tokenizer(prompts, padding=True, truncation=True, max_length=512,
                       return_tensors='pt').to(device)
    yes_id = tokenizer.convert_tokens_to_ids('yes')
    no_id = tokenizer.convert_tokens_to_ids('no')
    with torch.inference_mode():
        logits = model(**inputs).logits[:, -1, [no_id, yes_id]].float()
        scores = torch.softmax(logits, dim=1)[:, 1].tolist()
    reranker_seconds = time.perf_counter() - started
    result = {
        'device': device,
        'embedding_shape': embedding_shape,
        'embedding_cosine': similarities,
        'reranker_scores': scores,
        'embedding_load_and_infer_seconds': round(embedding_seconds, 3),
        'reranker_load_and_infer_seconds': round(reranker_seconds, 3),
        'cuda_peak_mib': round(torch.cuda.max_memory_allocated() / 1048576, 1) if device == 'cuda' else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if embedding_shape != [3, 1024] or not all(0 <= score <= 1 for score in scores):
        raise SystemExit('Unexpected model output')


if __name__ == '__main__':
    main()
