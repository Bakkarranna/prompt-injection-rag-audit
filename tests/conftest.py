"""Shared test fixtures.

The unit tests run without Ollama and without downloading an embedding model:
they use the deterministic hashing embedder and a scripted fake LLM whose
answers are chosen from the question, so the assertions exercise the scoring and
the defence conditions rather than the model.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("RAGAUDIT_EMBEDDER", "deterministic")
os.environ.setdefault("RAGAUDIT_NO_LLM", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class FakeLLM:
    """A scripted client with the surface ``evaluate.run_audit`` uses.

    It answers from a table of substrings so a test can make the model leak, or
    refuse, without calling a real server. Every call is recorded, which also
    lets a test assert that the audit makes exactly one call per attempt.
    """

    def __init__(
        self, rules: list[tuple[str, str]] | None = None, default: str = "refused"
    ) -> None:
        self.rules = rules or []
        self.default = default
        self.calls: list[dict] = []
        self.unloaded = False

    def generate(self, prompt: str, system: str) -> object:
        from ragaudit.llm import Completion

        self.calls.append({"prompt": prompt, "system": system})
        text = self.default
        for needle, reply in self.rules:
            if needle in prompt:
                text = reply
                break
        return Completion(
            text=text,
            prompt_tokens=100 + 7 * len(self.calls),
            completion_tokens=20,
            latency_ms=150.0,
            model="fake-model",
        )

    def unload(self) -> None:
        self.unloaded = True


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def data_dir():
    return str(DATA_DIR)
