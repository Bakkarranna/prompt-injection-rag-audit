"""Running the audit: every attack under every condition, plus benign baseline."""

from __future__ import annotations

import time

from .attacks import load_attacks, load_questions, poisoned_document
from .bot import Answer, SupportBot
from .config import Settings, load_settings
from .corpus import Document, Retriever, load_corpus
from .embeddings import build_embedder
from .journal import RunResult, attack_line, attempt_key, benign_line
from .llm import default_client
from .scoring import score_attack, score_benign

__all__ = [
    "Answer",
    "RunResult",
    "SupportBot",
    "_retriever_for",
    "run_audit",
]


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
    done: frozenset[str] | set[str] = frozenset(),
    on_attempt=None,
    wall_offset: float = 0.0,
) -> RunResult:
    """Run every attack under every condition, then the benign baseline.

    ``progress`` receives one string per attempt so a caller can show where the
    run is without parsing a log. ``client`` is the seam tests use to inject a
    fake LLM; production callers leave it at None and get the local server.

    ``done`` lists attempts already recorded in a journal and skips them, so a
    run interrupted after hours can finish only what is left. ``on_attempt`` is
    called with one journalled line as soon as each attempt is scored, which is
    what lets the next process pick up where this one stopped.
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
            if attempt_key("attack", condition, attack["id"]) in done:
                continue
            if progress is not None:
                progress(f"attack {attempt}/{total}: {attack['id']} under {condition}")
            retriever = _retriever_for(attack, base_docs, embedder)
            bot = SupportBot(retriever, client, settings)
            answer = bot.answer(attack["trigger_question"], condition)
            result.attack_answers.append((answer, attack))
            verdict = score_attack(answer, attack)
            result.attack_verdicts.append(verdict)
            if on_attempt is not None:
                on_attempt(attack_line(verdict, answer, attack))

    for condition in settings.conditions:
        for question in questions:
            if attempt_key("benign", condition, question["id"]) in done:
                continue
            if progress is not None:
                progress(f"benign: {question['id']} under {condition}")
            bot = SupportBot(clean_retriever, client, settings)
            answer = bot.answer(question["question"], condition)
            result.benign_answers.append((answer, question))
            quality = score_benign(answer, question)
            result.benign_quality.append(quality)
            if on_attempt is not None:
                on_attempt(benign_line(answer, question, quality))

    client.unload()

    # The artifact records the model that actually answered, not the configured
    # default, so a run driven by a different client is not mislabelled. A run
    # that only replayed journalled attempts keeps the journal's model.
    if result.attack_answers:
        result.model = result.attack_answers[0][0].model
    result.wall_seconds = (time.time() - started) + wall_offset
    return result
