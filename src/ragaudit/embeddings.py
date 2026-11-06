"""Sentence embeddings.

Vector texts are L2-normalised so a dot product equals cosine similarity.

A deterministic hashing embedder is used when ``RAGAUDIT_EMBEDDER=deterministic``.
It maps a text to a stable pseudo-random vector derived from its tokens, which
makes the unit tests deterministic without downloading a model. Because a hash
embedder breaks shared words into the same buckets, it still ranks documents
that repeat a query word highly, which is the behaviour the retrieval-poisoning
tests rely on.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np

# Kept in sync with README.md.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_VECTOR_DIM = 384


def _normalise(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return rows / norms


class DeterministicEmbedder:
    """A hashing embedder with no model download, for tests only."""

    dim = _VECTOR_DIM

    def encode(self, texts: list[str]) -> np.ndarray:
        rows = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                rows[row, bucket] += sign
        return _normalise(rows)


class SentenceTransformerEmbedder:
    """The real model, loaded lazily so imports stay cheap."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.model_name = model_name
        self._model = SentenceTransformer(
            model_name,
            # Shard the weights so the load does not materialise a second copy
            # of every tensor on a machine with a small page file.
            model_kwargs={"low_cpu_mem_usage": True},
        )
        size = getattr(self._model, "get_embedding_dimension", None)
        self.dim = int(size() if callable(size) else size)
        # Every distinct text is embedded once. A run re-indexes the corpus for
        # every attack it evaluates, so without this the same documents are
        # encoded fifty times over and the allocator fragments until a small
        # allocation fails on a machine that is not short of memory per se.
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        missing = [text for text in texts if text not in self._cache]
        if missing:
            fresh = np.asarray(self._model.encode(missing), dtype=np.float32)
            for text, vector in zip(missing, fresh, strict=True):
                self._cache[text] = vector
        return _normalise(np.asarray([self._cache[text] for text in texts], dtype=np.float32))


def build_embedder(settings=None):
    """Return the deterministic embedder for tests, the real one for a run."""

    if os.environ.get("RAGAUDIT_EMBEDDER") == "deterministic":
        return DeterministicEmbedder()
    name = getattr(settings, "model_name", DEFAULT_MODEL)
    return SentenceTransformerEmbedder(name)
