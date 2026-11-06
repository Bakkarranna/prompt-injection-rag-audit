"""Running the audit: every attack under every condition, plus benign baseline."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .attacks import load_attacks, load_questions, poisoned_document
from .bot import Answer, SupportBot
from .config import Settings, load_settings
from .corpus import Document, Retriever, load_corpus
from .embeddings import build_embedder
from .llm import default_client
from .scoring import score_attack, score_benign


@dataclass
class RunResult:
    """Everything produced by one full audit run."""

    settings: Settings
    model: str
    attack_answers: list[tuple[Answer, dict]] = field(default_factory=list)
    attack_verdicts: list = field(default_factory=list)
    benign_answers: list[tuple[Answer, dict]] = field(default_factory=list)
    benign_quality: list = field(default_factory=list)
    wall_seconds: float = 0.0


def _retriever_for(
    attack: dict | None,
    base_docs: list[Document],
    embedder,
) -> Retriever:
    """Build the retrieval index an attack actually sees.

    A document-surface attack is injected into the corpus before retrieval, which
    is what an attacker who can get one page indexed achieves. A question-surface
    attack is sent through the clean corpus, because the payload is in the
    question itself.
    """

    docs = list(base_docs)
    if attack is not None:
        poisoned = poisoned_document(attack)
        if poisoned is not None:
            docs.append(poisoned)
    return Retriever(docs, embedder)


def run_audit(
    settings: Settings | None = None,
    data_dir: str = "data",
    progress=None,
    client=None,
) -> RunResult:
    """Run every attack under every condition, then the benign baseline.

    ``progress`` receives one string per attempt so a caller can show where the
    run is without parsing a log. ``client`` is the seam tests use to inject a
    fake LLM; production callers leave it at None and get the local server.
    """

    settings = settings or load_settings()
    started = time.time()

    if client is None:
        client = default_client(settings)
    if client is None:
        raise RuntimeError(
            "no LLM available. Start Ollama on "
            f"{settings.ollama_url} with the {settings.ollama_model} model "
            "pulled, or set RAGAUDIT_NO_LLM=1 for a dry structural check."
        )

    attacks = load_attacks(f"{data_dir}/attacks.json")
    questions = load_questions(f"{data_dir}/questions.json")
    base_docs = load_corpus(f"{data_dir}/corpus.json")
    embedder = build_embedder(settings)

    # The clean corpus is indexed once and reused; each document-surface attack
    # adds one injected page on top of a fresh index.
    clean_retriever = Retriever(base_docs, embedder)
    result = RunResult(settings=settings, model=settings.ollama_model)

    total = len(attacks) * len(settings.conditions)
    attempt = 0
    for condition in settings.conditions:
        for attack in attacks:
            attempt += 1
            if progress is not None:
                progress(f"attack {attempt}/{total}: {attack['id']} under {condition}")
            retriever = _retriever_for(attack, base_docs, embedder)
            bot = SupportBot(retriever, client, settings)
            answer = bot.answer(attack["trigger_question"], condition)
            result.attack_answers.append((answer, attack))
            result.attack_verdicts.append(score_attack(answer, attack))

    for condition in settings.conditions:
        for question in questions:
            if progress is not None:
                progress(f"benign: {question['id']} under {condition}")
            bot = SupportBot(clean_retriever, client, settings)
            answer = bot.answer(question["question"], condition)
            result.benign_answers.append((answer, question))
            result.benign_quality.append(score_benign(answer, question))

    client.unload()

    # The artifact records the model that actually answered, not the configured
    # default, so a run driven by a different client is not mislabelled.
    if result.attack_answers:
        result.model = result.attack_answers[0][0].model
    result.wall_seconds = time.time() - started
    return result
