"""One line per attempt, written as it happens, so a long run can be resumed.

A 720-attempt run takes hours. If the process stops part way through, starting
again from zero throws away every answer already paid for. The journal records
each attempt as JSONL as it happens; the next run loads the journal, skips the
attempts it already covers and only asks the model for what is missing. Recorded
answers are replayed verbatim, so a resumed run produces the same artifact an
uninterrupted one would.

Wall time is the part a resume cannot recover. A completed run segment writes a
close line carrying how long it took. An interrupted segment has no close line,
so only its recorded attempt time is counted; the clock time an interrupted run
ran out is never reported as if it had produced results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .bot import Answer
from .config import Settings
from .scoring import Quality, Verdict

META = "meta"
CLOSE = "close"
ATTACK = "attack"
BENIGN = "benign"

_VERDICT_FIELDS = (
    "attack_id",
    "family",
    "surface",
    "condition",
    "success",
    "marker",
    "blocked",
    "reason",
)


@dataclass
class RunResult:
    """Everything produced by one full audit run, journalled or live.

    It lives here, beside the code that rebuilds one from recorded attempts, so
    that a resumed run and a fresh run fill the same shape.
    """

    settings: Settings
    model: str
    attack_answers: list[tuple[Answer, dict]] = field(default_factory=list)
    attack_verdicts: list = field(default_factory=list)
    benign_answers: list[tuple[Answer, dict]] = field(default_factory=list)
    benign_quality: list = field(default_factory=list)
    wall_seconds: float = 0.0


def attempt_key(kind: str, condition: str, item_id: str) -> str:
    """The identity of one attempt: its input and the condition it ran under."""

    return f"{kind}|{condition}|{item_id}"


def meta_line(started_at: float, model: str) -> dict:
    """Written when a run segment starts."""

    return {"kind": META, "started_at": started_at, "model": model}


def close_line(duration_seconds: float) -> dict:
    """Written when a run segment finishes, with how long it took."""

    return {"kind": CLOSE, "duration_seconds": round(duration_seconds, 3)}


def attack_line(verdict: Verdict, answer: Answer, attack: dict) -> dict:
    """One attack attempt in the shape the artifact needs."""

    return {
        "kind": ATTACK,
        **{field: getattr(verdict, field) for field in _VERDICT_FIELDS},
        "marker_text": attack.get("marker_text"),
        "question": answer.question,
        "answer": answer.text,
        "retrieved": [doc_id for doc_id, _ in answer.retrieval],
        "blocked": answer.blocked,
        "prompt_tokens": answer.prompt_tokens,
        "completion_tokens": answer.completion_tokens,
        "latency_ms": round(answer.latency_ms, 1),
    }


def benign_line(answer: Answer, question: dict, quality: Quality) -> dict:
    """One benign attempt in the shape the artifact needs."""

    return {
        "kind": BENIGN,
        "question_id": question["id"],
        "condition": answer.condition,
        "grounded": quality.grounded,
        "expected_doc_seen": quality.expected_doc_seen,
        "matched_keywords": list(quality.matched_keywords),
        "answer": answer.text,
        "prompt_tokens": answer.prompt_tokens,
        "completion_tokens": answer.completion_tokens,
        "latency_ms": round(answer.latency_ms, 1),
    }


def line_key(line: dict) -> str:
    """The attempt a journal line covers."""

    if line["kind"] == ATTACK:
        return attempt_key(ATTACK, line["condition"], line["attack_id"])
    return attempt_key(BENIGN, line["condition"], line["question_id"])


def read_lines(path: str | Path) -> list[dict]:
    """Read a journal, stopping at a truncated last line.

    A stopped process can be cut mid-write. An unparsable final line describes an
    attempt that never completed, so it is dropped instead of being trusted.
    """

    path = Path(path)
    if not path.exists():
        return []
    lines: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                break
    return lines


def done_keys(lines: list[dict]) -> set[str]:
    """Every attempt the journal already covers."""

    return {line_key(line) for line in lines if "kind" in line and line["kind"] in (ATTACK, BENIGN)}


def replay_seconds(lines: list[dict]) -> float:
    """Attempt time already spent and recorded, in seconds."""

    return sum(line.get("latency_ms", 0.0) for line in lines) / 1000.0


def known_wall_seconds(lines: list[dict]) -> float:
    """How much wall time the journal can prove was spent.

    Closed segments contribute their measured duration. An unclosed segment
    contributes only its attempt time, because how long it was alive in total
    is not recorded anywhere.
    """

    closed = sum(
        float(line["duration_seconds"]) for line in lines if line.get("kind") == CLOSE
    )
    last_close = max(
        (index for index, line in enumerate(lines) if line.get("kind") == CLOSE),
        default=-1,
    )
    return closed + replay_seconds(lines[last_close + 1 :])


def _answer(line: dict) -> Answer:
    return Answer(
        question=line.get("question", ""),
        condition=line["condition"],
        system_prompt="",
        prompt="",
        text=line["answer"],
        retrieval=[(doc_id, 0.0) for doc_id in line.get("retrieved", [])],
        blocked=line.get("blocked", False),
        prompt_tokens=line["prompt_tokens"],
        completion_tokens=line["completion_tokens"],
        latency_ms=line["latency_ms"],
        model=line.get("model", ""),
    )


def rebuild(
    lines: list[dict], settings, model: str, wall_seconds: float = 0.0
) -> RunResult:
    """Rebuild a result object from journalled attempts.

    Verdicts and scores travel with their line, so rebuilding re-reads them
    instead of re-deriving them from the answers. The prompt a replayed attempt
    was given is not carried, because nothing downstream reads it and the model
    call has already happened.
    """

    result = RunResult(settings=settings, model=model, wall_seconds=wall_seconds)
    for line in lines:
        if line["kind"] == ATTACK:
            result.attack_answers.append((_answer(line), {"id": line["attack_id"]}))
            result.attack_verdicts.append(
                Verdict(**{field: line[field] for field in _VERDICT_FIELDS})
            )
        elif line["kind"] == BENIGN:
            result.benign_answers.append(
                (_answer(line), {"id": line["question_id"]})
            )
            result.benign_quality.append(
                Quality(
                    grounded=line["grounded"],
                    expected_doc_seen=line["expected_doc_seen"],
                    matched_keywords=tuple(line["matched_keywords"]),
                )
            )
    return result


def merge(base: RunResult, fresh: RunResult) -> RunResult:
    """Join a replayed result with the attempts this run just made.

    Wall time adds up: the replayed part is time already spent, not free.
    """

    merged = RunResult(
        settings=fresh.settings,
        model=fresh.model if fresh.attack_answers else base.model,
    )
    for name in ("attack_answers", "attack_verdicts", "benign_answers", "benign_quality"):
        merged.__dict__[name] = getattr(base, name) + getattr(fresh, name)
    merged.wall_seconds = base.wall_seconds + fresh.wall_seconds
    return merged
