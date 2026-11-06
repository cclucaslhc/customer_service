"""Offline-only local Embedding and Reranker inference.

No model repository ID or network URL is passed to Transformers. Callers must
verify the pinned local weights before loading and run this in a bounded Worker.
"""

from pathlib import Path
from contextlib import nullcontext
from copy import copy

from app.rag_model_manifest import EMBEDDING_WEIGHT, RERANKER_WEIGHT, verify_weight


EMBEDDING_INSTRUCTION = 'Given a customer support question, retrieve passages that answer it.'
RERANK_INSTRUCTION = 'Given a customer support question, retrieve passages that answer it'
RERANK_PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query '
                 'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
                 '<|im_start|>user\n')
RERANK_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


def _measurement(gateway,purpose,items):
    from app.local_inference_usage import LocalInferenceObservation
    meter=getattr(gateway,'usage_meter',None)
    return meter.measure(purpose,items,items*gateway.max_length) if meter else nullcontext(LocalInferenceObservation())


def _with_meter(gateway,meter):
    if not callable(getattr(meter,'measure',None)):
        raise ValueError('Explicit local inference meter required')
    result=copy(gateway)
    result.usage_meter=meter
    return result


class LocalEmbeddingGateway:
    def with_meter(self,meter):
        return _with_meter(self,meter)

    def __init__(self, directory: Path, *, device: str = 'cpu', max_length: int = 1024):
        if max_length < 64 or max_length > 8192:
            raise ValueError('Invalid Embedding input limit')
        verify_weight(directory, EMBEDDING_WEIGHT)
        import torch
        import torch.nn.functional as functional
        from transformers import AutoModel, AutoTokenizer

        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable')
        if device not in {'cpu', 'cuda'}:
            raise ValueError('Invalid local Embedding device')
        self.torch = torch
        self.functional = functional
        self.device = device
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True,
                                                        trust_remote_code=False, padding_side='left')
        self.model = AutoModel.from_pretrained(directory, local_files_only=True,
                                                trust_remote_code=False,
                                                dtype=torch.float16 if device == 'cuda' else torch.float32).to(device).eval()

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > 16 or any(not text or len(text) > 16000 for text in texts):
            raise ValueError('Embedding batch exceeds limits')
        with _measurement(self,'embedding_query' if query else 'embedding_document',len(texts)) as observation:
            return self._encode(texts,query,observation)

    def _encode(self,texts,query,observation):
        values = [f'Instruct: {EMBEDDING_INSTRUCTION}\nQuery:{text}' for text in texts] if query else texts
        inputs = self.tokenizer(values, padding=True, truncation=True,
                                max_length=self.max_length, return_tensors='pt').to(self.device)
        observation.observe_tokens(int(inputs['attention_mask'].sum().item()))
        with self.torch.inference_mode():
            output = self.model(**inputs)
            vector = self.functional.normalize(output.last_hidden_state[:, -1, :].float(), p=2, dim=1)
        if vector.shape != (len(texts), 1024) or not self.torch.isfinite(vector).all():
            raise RuntimeError('Invalid Embedding output')
        return vector.cpu().tolist()


class LocalReranker:
    def with_meter(self,meter):
        return _with_meter(self,meter)

    def __init__(self, directory: Path, *, device: str = 'cpu', max_length: int = 1024):
        if max_length < 128 or max_length > 8192:
            raise ValueError('Invalid Reranker input limit')
        verify_weight(directory, RERANKER_WEIGHT)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable')
        if device not in {'cpu', 'cuda'}:
            raise ValueError('Invalid local Reranker device')
        self.torch = torch
        self.device = device
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True,
                                                        trust_remote_code=False, padding_side='left')
        self.model = AutoModelForCausalLM.from_pretrained(directory, local_files_only=True,
                                                           trust_remote_code=False,
                                                           dtype=torch.float16 if device == 'cuda' else torch.float32).to(device).eval()
        self.no_id = self.tokenizer.convert_tokens_to_ids('no')
        self.yes_id = self.tokenizer.convert_tokens_to_ids('yes')
        if self.no_id == self.yes_id or min(self.no_id, self.yes_id) < 0:
            raise RuntimeError('Invalid Reranker label tokens')
        self.prefix_tokens = self.tokenizer.encode(RERANK_PREFIX, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(RERANK_SUFFIX, add_special_tokens=False)
        if len(self.prefix_tokens) + len(self.suffix_tokens) + 32 >= max_length:
            raise ValueError('Reranker input limit leaves no document budget')

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if not query or len(query) > 8000 or len(documents) > 16 or any(not item or len(item) > 16000 for item in documents):
            raise ValueError('Reranker batch exceeds limits')
        with _measurement(self,'rerank',len(documents)) as observation:
            return self._score(query,documents,observation)

    def _score(self,query,documents,observation):
        budget = self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens)
        contents = [f'<Instruct>: {RERANK_INSTRUCTION}\n<Query>:{query}\n<Document>:{doc}'
                    for doc in documents]
        encoded = self.tokenizer(contents, add_special_tokens=False, truncation=True,
                                 max_length=budget)['input_ids']
        assembled = [self.prefix_tokens + tokens + self.suffix_tokens for tokens in encoded]
        inputs = self.tokenizer.pad({'input_ids': assembled}, padding=True, return_tensors='pt').to(self.device)
        observation.observe_tokens(int(inputs['attention_mask'].sum().item()))
        with self.torch.inference_mode():
            logits = self.model(**inputs).logits[:, -1, [self.no_id, self.yes_id]].float()
            scores = self.torch.softmax(logits, dim=1)[:, 1]
        if not self.torch.isfinite(scores).all():
            raise RuntimeError('Invalid Reranker output')
        return scores.cpu().tolist()
