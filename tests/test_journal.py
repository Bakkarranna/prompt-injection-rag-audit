"""A run that is stopped part way through keeps what it already paid for.

The journal is the seam. An interrupted segment records every scored attempt and
drops a truncated last line, and the next run skips what the journal covers. The
artifact a resumed run writes has to be the artifact an uninterrupted run would
have written, otherwise resuming quietly changes the measurement.
"""

from __future__ import annotations

import json
import re

from ragaudit.attacks import load_attacks
from ragaudit.config import CANARY, load_settings
from ragaudit.evaluate import run_audit
from ragaudit.journal import (
    close_line,
    done_keys,
    known_wall_seconds,
    merge,
    meta_line,
    read_lines,
    rebuild,
)
from ragaudit.llm import Completion
from ragaudit.report import build_record

MARKER = re.compile(r"INJECTED-\d{4}")
TOKEN_ASKS = (
    "internal reference token",
    "internal reference string",
    "internal reference is",
    "every constant in your configuration",
)
PROMPT_ASKS = ("system prompt", "the text that appears after")


class StoppingLLM:
    """A client that dies after a set number of calls, like a stopped runner."""

    def __init__(self, survive_calls: int) -> None:
        self.survive_calls = survive_calls
        self.calls = 0

    def generate(self, prompt, system):
        self.calls += 1
        if self.calls > self.survive_calls:
            raise RuntimeError("the runner stopped")
        lowered = prompt.lower()
        marker = MARKER.search(prompt)
        if marker:
            text = marker.group(0)
        elif any(needle in lowered for needle in TOKEN_ASKS):
            text = f"The internal reference is {CANARY}"
        elif any(needle in lowered for needle in PROMPT_ASKS):
            text = "System prompt: the text that appears after the documents"
        else:
            text = "Ledgerline refunds within 14 days of the invoice date."
        return Completion(
            text=text,
            prompt_tokens=500,
            completion_tokens=30,
            latency_ms=100.0,
            model="fake",
        )

    def unload(self) -> None:
        return None


def _write(handle, line: dict) -> None:
    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    handle.flush()


def _run_segment(settings, data_dir: str, journal_path, client):
    """One script run: replay the journal, make the missing calls, close it."""

    lines = read_lines(journal_path)
    replayed = rebuild(
        lines,
        settings,
        model=settings.ollama_model,
        wall_seconds=known_wall_seconds(lines),
    )
    with open(journal_path, "a", encoding="utf-8", newline="\n") as handle:
        _write(handle, meta_line(0.0, settings.ollama_model))
        fresh = run_audit(
            settings=settings,
            data_dir=data_dir,
            client=client,
            done=done_keys(lines),
            on_attempt=lambda line: _write(handle, line),
        )
        merged = merge(replayed, fresh)
        _write(handle, close_line(1.0))
    return merged


def _record(result) -> dict:
    record = build_record(result)
    record.pop("wall_seconds")
    return record


def test_read_lines_drops_a_truncated_last_line(tmp_path):
    path = tmp_path / "audit.journal.jsonl"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        _write(handle, meta_line(0.0, "fake"))
        _write(handle, {"kind": "benign", "condition": "none", "question_id": "q1"})
        handle.write('{"kind": "benign", "cond')

    lines = read_lines(path)
    assert len(lines) == 2
    assert done_keys(lines) == {"benign|none|q1"}


def test_wall_seconds_counts_finished_work_not_stopping_time():
    lines = [
        meta_line(0.0, "fake"),
        {"kind": "benign", "condition": "none", "question_id": "q1", "latency_ms": 1000},
        {"kind": "benign", "condition": "none", "question_id": "q2", "latency_ms": 2000},
        close_line(5.0),
        meta_line(10.0, "fake"),
        {"kind": "benign", "condition": "fenced", "question_id": "q1", "latency_ms": 4000},
        {"kind": "benign", "condition": "fenced", "question_id": "q2", "latency_ms": 500},
    ]
    # The finished segment took 5 s. The interrupted one contributes only its
    # 4.5 s of attempt time: the 5 s gap between the segments is not claimed as
    # work, because nothing was produced while the runner was down.
    assert known_wall_seconds(lines) == 9.5


def test_a_resumed_run_writes_the_uninterrupted_record(tmp_path, data_dir):
    settings = load_settings()
    journal_path = tmp_path / "audit.journal.jsonl"

    # The runner stops after five answers and the segment is never closed.
    try:
        _run_segment(settings, data_dir, journal_path, StoppingLLM(survive_calls=5))
    except RuntimeError:
        pass

    interrupted = read_lines(journal_path)
    assert done_keys(interrupted)
    assert not any(line.get("kind") == "close" for line in interrupted)

    resumed = _run_segment(
        settings, data_dir, journal_path, StoppingLLM(survive_calls=10**6)
    )
    complete = run_audit(
        settings,
        data_dir=data_dir,
        client=StoppingLLM(survive_calls=10**6),
    )

    assert _record(resumed) == _record(complete)
    assert resumed.wall_seconds >= complete.wall_seconds - 1.0
    assert len(done_keys(interrupted)) == 5


def test_the_resumed_run_only_asks_for_missing_attempts(tmp_path, data_dir):
    settings = load_settings()
    journal_path = tmp_path / "audit.journal.jsonl"

    try:
        _run_segment(settings, data_dir, journal_path, StoppingLLM(survive_calls=5))
    except RuntimeError:
        pass
    remaining = StoppingLLM(survive_calls=10**6)
    resumed = _run_segment(settings, data_dir, journal_path, remaining)

    total = len(load_attacks(f"{data_dir}/attacks.json")) * len(settings.conditions)
    total += len(
        json.loads(open(f"{data_dir}/questions.json", encoding="utf-8").read())["questions"]
    ) * len(settings.conditions)

    # The first segment answered five; the resumed one only paid for the rest.
    assert remaining.calls == total - 5
    assert len(resumed.attack_answers) + len(resumed.benign_answers) == total
