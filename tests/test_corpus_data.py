"""The pre-registered data files are the experiment, so they get their own checks.

These run without an LLM or an embedding model and guard the invariants the
audit depends on: every attack names exactly one marker, the two surfaces are
both exercised, and every benign question points at a document that exists.
"""

from __future__ import annotations

import json

from ragaudit.attacks import load_attacks, load_questions, poisoned_document
from ragaudit.config import load_settings
from ragaudit.corpus import load_corpus

SETTINGS = load_settings()


def _load(path):
    return json.load(open(path, encoding="utf-8"))


def test_corpus_has_no_duplicate_ids(data_dir):
    docs = load_corpus(f"{data_dir}/corpus.json")
    assert len({d.doc_id for d in docs}) == len(docs)
    assert len(docs) == 12


def test_attack_file_covers_every_family_and_both_surfaces(data_dir):
    attacks = load_attacks(f"{data_dir}/attacks.json")
    assert len(attacks) == 200

    families = {a["family"] for a in attacks}
    assert families == {
        "direct_override",
        "exfiltration",
        "indirect_injection",
        "retrieval_poisoning",
    }

    surfaces = {a["surface"] for a in attacks}
    assert surfaces == {"question", "document"}
    question_surface = [a for a in attacks if a["surface"] == "question"]
    document_surface = [a for a in attacks if a["surface"] == "document"]
    assert len(question_surface) == len(document_surface) == 100


def test_every_attack_uses_a_registered_marker(data_dir):
    attacks = _load(f"{data_dir}/attacks.json")["attacks"]
    markers = {a["success_marker"] for a in attacks}
    assert markers <= set(SETTINGS.success_markers)


def test_marker_emitted_attacks_carry_marker_text(data_dir):
    attacks = _load(f"{data_dir}/attacks.json")["attacks"]
    for attack in attacks:
        if attack["success_marker"] == "marker_emitted":
            assert attack["marker_text"], attack["id"]


def test_document_surface_attacks_carry_an_injectable_document(data_dir):
    attacks = _load(f"{data_dir}/attacks.json")["attacks"]
    for attack in attacks:
        if attack["surface"] == "document":
            injected = attack["injected_document"]
            assert injected is not None and injected["title"] and injected["text"], attack["id"]
            assert poisoned_document(attack) is not None
        else:
            assert attack["injected_document"] is None, attack["id"]
            assert poisoned_document(attack) is None


def test_benign_questions_reference_real_documents(data_dir):
    questions = load_questions(f"{data_dir}/questions.json")
    doc_ids = {d.doc_id for d in load_corpus(f"{data_dir}/corpus.json")}
    assert len(questions) == 40
    for question in questions:
        assert question["expected_doc"] in doc_ids, question["id"]
        assert question["expected_keywords"], question["id"]


def test_settings_match_the_data_files(data_dir):
    attacks = _load(f"{data_dir}/attacks.json")["attacks"]
    assert SETTINGS.top_k == 3
    assert len(SETTINGS.conditions) == 3
    assert {a["family"] for a in attacks} == {
        "direct_override",
        "exfiltration",
        "indirect_injection",
        "retrieval_poisoning",
    }
