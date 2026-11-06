"""The document corpus and retrieval over it.

Retrieval is a plain cosine top-k over normalised vectors, computed with numpy
rather than an ANN index: the corpus is 13 documents at most, so an index would
add a dependency and hide the rankings this audit is specifically trying to see.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    """One retrievable document."""

    doc_id: str
    title: str
    text: str


def load_corpus(path: str | Path) -> list[Document]:
    """Read the owned corpus. Every document is trusted product documentation."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    docs = [Document(d["id"], d["title"], d["text"]) for d in payload["documents"]]
    ids = [d.doc_id for d in docs]
    if len(set(ids)) != len(ids):
        raise ValueError("corpus contains duplicate document ids")
    return docs


def load_questions(path: str | Path) -> list[dict]:
    """Read the benign questions and their pre-registered expected keywords."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(payload["questions"])


def load_attacks(path: str | Path) -> list[dict]:
    """Read the pre-registered attack set."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    attacks = list(payload["attacks"])
    ids = [a["id"] for a in attacks]
    if len(set(ids)) != len(ids):
        raise ValueError("attack file contains duplicate attack ids")
    return attacks


class Retriever:
    """Cosine top-k retrieval over an embedder."""

    def __init__(self, documents: list[Document], embedder) -> None:
        self.documents = list(documents)
        self.embedder = embedder
        self._titles = [f"{d.title}. {d.text}" for d in self.documents]
        self._matrix = embedder.encode(self._titles)

    def search(self, query: str, top_k: int) -> list[tuple[Document, float]]:
        """Return the top_k documents for a query, best first."""

        vector = self.embedder.encode([query])[0]
        scores = self._matrix @ vector
        order = sorted(range(len(self.documents)), key=lambda i: (-scores[i], i))
        return [(self.documents[i], float(scores[i])) for i in order[:top_k]]
