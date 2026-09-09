"""
core/reranker.py
Thread-safe, lazy-loaded singleton for BAAI/bge-reranker-v2-m3.
load the reranker once and using it whenever your RAG system needs to rerank retrieved chunks.
"""

import logging
import threading
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

log = logging.getLogger(__name__)

#System -level CPU thread tuning
torch.set_num_threads(8)

_RERANKER_LOCK = threading.Lock()
_TOKENIZER_INSTANCE = None
_MODEL_INSTANCE = None


MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

def _detect_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def get_reranker():
    """Lazy load tokenizer and model in a thread-safe manner."""
    global _TOKENIZER_INSTANCE, _MODEL_INSTANCE

    if _MODEL_INSTANCE is None:
        with _RERANKER_LOCK:
            if _MODEL_INSTANCE is None:
                device = _detect_device()
                log.info(f"Loading '{MODEL_NAME}' on device='{device}'...")

                tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
                model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)

                if device == "cuda":
                    model = model.half().to("cuda")  # Use FP16 on GPU
                else:
                    model = model.to(device)

                model.eval()

                _TOKENIZER_INSTANCE = tokenizer
                _MODEL_INSTANCE = model
                log.info(f"Reranker model '{MODEL_NAME}' loaded successfully.")

    return _TOKENIZER_INSTANCE, _MODEL_INSTANCE


def rerank_documents(query: str, documents: list[str], batch_size: int = 16) -> list[float]:
    """Score candidate documents against a query in batches."""
    if not documents:
        return []

    tokenizer, model = get_reranker()
    device = next(model.parameters()).device
    all_scores = []

    pairs = [[query, doc] for doc in documents]

    # Process in mini-batches to optimize memory usage & keep CPU latency consistent
    with torch.inference_mode():
        for i in range(0, len(pairs), batch_size):
            batch_pairs = pairs[i : i + batch_size]
            
            inputs = tokenizer(
                batch_pairs,
                padding=True,
                truncation=True,
                max_length=512,  # 512 tokens max context window
                return_tensors="pt"
            ).to(device)

            logits = model(**inputs).logits.view(-1)
            scores = torch.sigmoid(logits).cpu().tolist()

            if isinstance(scores, float):
                scores = [scores]
                
            all_scores.extend(scores)

    return all_scores
    