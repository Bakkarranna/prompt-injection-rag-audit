"""The audit pipeline, run end to end with a fake LLM.

These tests do not need Ollama or a model download: they use the deterministic
hashing embedder and a scripted client, so they check that the pipeline wires
retrieval, the three defence conditions, scoring and aggregation together and
that the artifacts it writes are shaped the way the report expects.
"""

from __future__ import annotations

import json
import re

from ragaudit.attacks import load_attacks, poisoned_document
from ragaudit.config import CANARY, SYSTEM_PROMPT_FINGERPRINT, load_settings
from ragaudit.corpus import Retriever, load_corpus
from ragaudit.embeddings import build_embedder
from ragaudit.evaluate import run_audit
from ragaudit.report import summarise, write_artifacts


class FakeLLM:
    """An unsafeguarded model.

    It follows any instruction it finds in the prompt, whether that instruction
    came from the customer's question or from a retrieved document. That makes
    the three defence conditions the only thing that differs between the runs,
    which is what the assertions below are about. It is deliberately not a
    model that resists injection, because a resisting fake would make the audit
    measure the fake instead of the bot.
    """

    # Attacks tell the bot to print a token such as INJECTED-4471.
    MARKER = re.compile(r"INJECTED-\d{4}")

    # Phrases that ask for the internal reference token or a config dump.
    TOKEN_ASKS = (
        "internal reference token",
        "internal reference string",
        "internal reference is",
        "every constant in your configuration",
    )

    # Phrases that ask for the system prompt itself.
    PROMPT_ASKS = (
        "system prompt",
        "the text that appears after",
    )

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, prompt, system):
        from ragaudit.llm import Completion

        self.calls.append({"prompt": prompt, "system": system})
        lowered = prompt.lower()

        text = "Ledgerline refunds within 14 days of the invoice date."
        marker = self.MARKER.search(prompt)
        if marker:
            text = marker.group(0)
        elif any(needle in lowered for needle in self.TOKEN_ASKS):
            text = f"The internal reference is {CANARY}"
        elif any(needle in lowered for needle in self.PROMPT_ASKS):
            text = f"System prompt: {SYSTEM_PROMPT_FINGERPRINT} and the canary"

        return Completion(
            text=text,
            prompt_tokens=500,
            completion_tokens=30,
            latency_ms=1000.0,
            model="fake",
        )

    def unload(self) -> None:
        return None


def test_default_client_is_none_without_ollama(monkeypatch):
    monkeypatch.setenv("RAGAUDIT_NO_LLM", "1")
    from ragaudit.llm import default_client

    assert default_client(load_settings()) is None


def test_run_audit_covers_every_attack_and_condition(data_dir, fake_llm):
    result = run_audit(load_settings(), data_dir=data_dir, client=FakeLLM())
    settings = load_settings()
    attacks = load_attacks(f"{data_dir}/attacks.json")
    questions = json.loads(open(f"{data_dir}/questions.json", encoding="utf-8").read())

    assert len(result.attack_answers) == len(attacks) * len(settings.conditions)
    assert len(result.benign_answers) == len(questions["questions"]) * len(settings.conditions)
    assert result.wall_seconds >= 0.0


def test_output_filter_withholds_a_leaking_answer(data_dir):
    result = run_audit(load_settings(), data_dir=data_dir, client=FakeLLM())
    summaries = {s.condition: s for s in summarise(result)}

    undefended = summaries["none"]
    filtered = summaries["fenced+filter"]
    assert undefended.successes > 0
    assert filtered.blocked > 0
    assert filtered.success_rate < undefended.success_rate

    # The filter only stops answers that leak the canary or the prompt
    # fingerprint. An attacker-chosen token that appears in neither is still
    # emitted, so the residual successes under fenced+filter are all
    # marker_emitted attacks. That is the honest cost of a string check.
    residual = [v for v in result.attack_verdicts if v.condition == "fenced+filter" and v.success]
    assert residual
    assert all(v.marker == "marker_emitted" for v in residual)


def test_benign_questions_stay_grounded_under_every_condition(data_dir):
    result = run_audit(load_settings(), data_dir=data_dir, client=FakeLLM())
    for summary in summarise(result):
        assert summary.benign_grounded > 0
        assert summary.benign_questions == 12


def test_keyword_stuffed_documents_win_retrieval(data_dir):
    """Retrieval poisoning has to win retrieval before it can do anything else.

    With the deterministic embedder, four of the five poisoning attacks beat the
    real document and appear in the top-k; poison-1 does not, which is why that
    attack can never reach the model in this run.
    """

    attacks = load_attacks(f"{data_dir}/attacks.json")
    corpus = load_corpus(f"{data_dir}/corpus.json")
    embedder = build_embedder()
    poisoned = [a for a in attacks if a["family"] == "retrieval_poisoning"]

    hits = 0
    for attack in poisoned:
        retriever = Retriever(corpus + [poisoned_document(attack)], embedder)
        ranked = [doc.doc_id for doc, _ in retriever.search(attack["trigger_question"], 3)]
        if f"injected::{attack['id']}" in ranked:
            hits += 1

    assert hits == 4, hits


def test_artifacts_round_trip(data_dir, tmp_path):
    result = run_audit(load_settings(), data_dir=data_dir, client=FakeLLM())
    json_path = str(tmp_path / "audit.json")
    jsonl_path = str(tmp_path / "audit.jsonl")
    record = write_artifacts(result, json_path, jsonl_path)

    assert record["counts"]["attempts"] == len(record["attacks"]) + len(record["benign"])
    assert [s["condition"] for s in record["summaries"]] == list(load_settings().conditions)

    lines = [json.loads(line) for line in open(jsonl_path, encoding="utf-8")]
    assert len(lines) == record["counts"]["attempts"]
    kinds = {line["kind"] for line in lines}
    assert kinds == {"attack", "benign"}
    for line in lines:
        assert line["model"] == "fake"
