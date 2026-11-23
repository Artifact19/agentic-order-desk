import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from .cache import TTLCache
from .config import MEMORY_ADJUSTMENT_RANGE, MEMORY_WEIGHTS, RECALL_MIN_SIMILARITY, RECALL_TOP_K


class SentenceEmbedder:
    def __init__(self, model_id: str):
        from sentence_transformers import SentenceTransformer
        import torch

        from .config import model_source

        self.model = SentenceTransformer(model_source(model_id), device="cuda" if torch.cuda.is_available() else "cpu")
        self.dimension = self.model.get_sentence_embedding_dimension()
        self.name = model_id

    def encode(self, texts: list) -> np.ndarray:
        vectors = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vectors, dtype="float32")


class HashingEmbedder:
    def __init__(self, dimension: int = 256):
        self.dimension = dimension
        self.name = f"hashing-{dimension}"

    def encode(self, texts: list) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype="float32")
        for row, text in enumerate(texts):
            for token in text.lower().split():
                token = token.strip(".,!?'\"()")
                digest = int(hashlib.md5(token.encode()).hexdigest(), 16)
                matrix[row, digest % self.dimension] += 1.0
            norm = np.linalg.norm(matrix[row])
            if norm:
                matrix[row] /= norm
        return matrix


def load_embedder(model_id: str, enabled: bool):
    if not enabled:
        return HashingEmbedder(), "hashing embedder (configuration)"
    try:
        return SentenceEmbedder(model_id), "sentence-transformer loaded"
    except Exception as error:
        return HashingEmbedder(), f"hashing fallback ({type(error).__name__})"


class LongTermMemoryStore:
    def __init__(self, directory: Path, embedder, embedding_cache: TTLCache):
        import faiss

        self._faiss = faiss
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.embedding_cache = embedding_cache
        self.index_path = self.directory / "memory.index"
        self.records_path = self.directory / "memory_records.json"
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if self.index_path.exists() and self.records_path.exists():
            index = self._faiss.read_index(str(self.index_path))
            if index.d == self.embedder.dimension:
                self.index = index
                self.records = json.loads(self.records_path.read_text(encoding="utf-8"))
                return
        self.index = self._faiss.IndexFlatIP(self.embedder.dimension)
        self.records = []

    def _persist(self):
        self._faiss.write_index(self.index, str(self.index_path))
        temporary = self.records_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.records, indent=2), encoding="utf-8")
        temporary.replace(self.records_path)

    def embed(self, text: str) -> np.ndarray:
        vector, _ = self.embedding_cache.get_or_load(("embedding", self.embedder.name, text),
                                                     lambda: self.embedder.encode([text]))
        return vector

    def add(self, memory_type: str, text: str, metadata: dict) -> dict:
        with self._lock:
            record = {"memory_id": f"m_{len(self.records) + 1:04d}", "memory_type": memory_type, "text": text,
                      "created_at": datetime.now().isoformat(timespec="seconds"), **metadata}
            self.index.add(self.embed(text))
            self.records.append(record)
            self._persist()
            return record

    def search(self, query: str, k: int = RECALL_TOP_K, min_similarity: float = RECALL_MIN_SIMILARITY) -> list:
        with self._lock:
            if self.index.ntotal == 0:
                return []
            scores, positions = self.index.search(self.embed(query), min(k, self.index.ntotal))
            return [{**self.records[p], "similarity": round(float(s), 3)}
                    for s, p in zip(scores[0], positions[0]) if p >= 0 and s >= min_similarity]

    def latest(self, memory_type: str):
        with self._lock:
            return next((r for r in reversed(self.records) if r["memory_type"] == memory_type), None)

    def clear(self):
        with self._lock:
            self.index = self._faiss.IndexFlatIP(self.embedder.dimension)
            self.records = []
            self._persist()

    def counts(self) -> dict:
        orders = sum(1 for r in self.records if r["memory_type"] == "order")
        return {"total": len(self.records), "orders": orders, "rejections": len(self.records) - orders}

    def __len__(self):
        return len(self.records)


def describe_order_memory(item: dict, request: str) -> str:
    return (f"Ordered {item['item_name']} from {item['restaurant_name']} at Rs {item['price']} "
            f"with {item['eta_minutes']} minute delivery for the request: '{request}'.")


def describe_rejection_memory(item: dict, request: str) -> str:
    return f"Rejected the recommendation {item['item_name']} from {item['restaurant_name']} for the request: '{request}'."


def compute_memory_adjustment(candidate: dict, recalled: list) -> tuple:
    adjustment, reasons = 0.0, []
    for memory in recalled:
        if memory["memory_type"] == "order":
            if memory["item_id"] == candidate["item_id"]:
                adjustment += MEMORY_WEIGHTS["approved_item"]
                reasons.append(f"ordered this item before ({memory['memory_id']})")
            elif memory["restaurant_id"] == candidate["restaurant_id"]:
                adjustment += MEMORY_WEIGHTS["approved_restaurant"]
                reasons.append(f"ordered from this restaurant before ({memory['memory_id']})")
        elif memory["memory_type"] == "rejection" and memory["item_id"] == candidate["item_id"]:
            adjustment += MEMORY_WEIGHTS["rejected_item"]
            reasons.append(f"rejected in an earlier session ({memory['memory_id']})")
    low, high = MEMORY_ADJUSTMENT_RANGE
    return round(min(max(adjustment, low), high), 2), reasons
