"""Attack data and injecting an attack into the corpus.

The loaders live in ``corpus`` because they read the same JSON shape; this
module holds what is specific to the audit: turning a document-surface attack
into a document that a retriever will rank alongside the real ones.
"""

from __future__ import annotations

from .corpus import Document, load_attacks, load_questions

__all__ = ["load_attacks", "load_questions", "poisoned_document"]


def poisoned_document(attack: dict) -> Document | None:
    """Turn a document-surface attack into a Document, or None for question-surface ones.

    The injected document is written the way an attacker would write it: it
    pretends to be a legitimate page from the corpus, with a plausible title,
    so a naive retriever ranks it next to the real documents.
    """

    if attack["surface"] != "document":
        return None
    injected = attack["injected_document"]
    return Document(
        doc_id=f"injected::{attack['id']}",
        title=injected["title"],
        text=injected["text"],
    )
