"""Aggregating a run into the numbers that go in the README.

Every number here is computed from recorded answers. Nothing is estimated, and a
figure that cannot be derived from the artifacts is not reported at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .evaluate import RunResult
from .journal import attack_line, benign_line


@dataclass(frozen=True)
class ConditionSummary:
    """One defence condition's headline numbers."""

    condition: str
    attacks: int
    successes: int
    success_rate: float
    blocked: int
    by_family: dict[str, dict[str, object]]
    by_marker: dict[str, dict[str, object]]
    benign_questions: int
    benign_grounded: int
    benign_grounded_rate: float
    median_latency_ms: float
    mean_prompt_tokens: float
    mean_completion_tokens: float


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def summarise(result: RunResult) -> list[ConditionSummary]:
    """Summarise one condition at a time, in the order they were run."""

    verdicts = result.attack_verdicts
    answers = result.attack_answers
    benign = result.benign_answers
    quality = result.benign_quality

    summaries: list[ConditionSummary] = []
    for condition in result.settings.conditions:
        cond_verdicts = [v for v in verdicts if v.condition == condition]
        cond_answers = [a for a, _ in answers if a.condition == condition]
        cond_benign = [a for a, _ in benign if a.condition == condition]
        cond_quality = [
            q
            for (a, _), q in zip(benign, quality, strict=True)
            if a.condition == condition
        ]

        by_family: dict[str, dict[str, object]] = {}
        for verdict in cond_verdicts:
            bucket = by_family.setdefault(verdict.family, {"attacks": 0, "successes": 0})
            bucket["attacks"] = int(bucket["attacks"]) + 1  # type: ignore[arg-type]
            bucket["successes"] = int(bucket["successes"]) + (1 if verdict.success else 0)  # type: ignore[arg-type]
        for bucket in by_family.values():
            attacks = int(bucket["attacks"])  # type: ignore[arg-type]
            bucket["success_rate"] = _rate(int(bucket["successes"]), attacks)  # type: ignore[arg-type]

        by_marker: dict[str, dict[str, object]] = {}
        for verdict in cond_verdicts:
            bucket = by_marker.setdefault(verdict.marker, {"attacks": 0, "successes": 0})
            bucket["attacks"] = int(bucket["attacks"]) + 1  # type: ignore[arg-type]
            bucket["successes"] = int(bucket["successes"]) + (1 if verdict.success else 0)  # type: ignore[arg-type]
        for bucket in by_marker.values():
            attacks = int(bucket["attacks"])  # type: ignore[arg-type]
            bucket["success_rate"] = _rate(int(bucket["successes"]), attacks)  # type: ignore[arg-type]

        successes = sum(1 for v in cond_verdicts if v.success)
        grounded = sum(1 for q in cond_quality if q.grounded)
        latencies = [a.latency_ms for a in cond_answers + cond_benign]
        summaries.append(
            ConditionSummary(
                condition=condition,
                attacks=len(cond_verdicts),
                successes=successes,
                success_rate=_rate(successes, len(cond_verdicts)),
                blocked=sum(1 for a in cond_answers if a.blocked),
                by_family=by_family,
                by_marker=by_marker,
                benign_questions=len(cond_benign),
                benign_grounded=grounded,
                benign_grounded_rate=_rate(grounded, len(cond_quality)),
                median_latency_ms=statistics.median(latencies) if latencies else 0.0,
                mean_prompt_tokens=statistics.fmean([a.prompt_tokens for a in cond_answers])
                if cond_answers
                else 0.0,
                mean_completion_tokens=statistics.fmean(
                    [a.completion_tokens for a in cond_answers]
                )
                if cond_answers
                else 0.0,
            )
        )
    return summaries


def build_record(result: RunResult) -> dict:
    """The whole run as one JSON-serialisable dictionary.

    This is the artifact the README numbers are read from, so it carries the
    inputs (model, conditions, counts) beside the every-attempt results.
    """

    verdicts = result.attack_verdicts
    return {
        "project": "prompt-injection-rag-audit",
        "model": result.model,
        "embedder": result.settings.embedder,
        "conditions": list(result.settings.conditions),
        "success_markers": list(result.settings.success_markers),
        "counts": {
            "attacks": len(verdicts),
            "benign_questions": len(result.benign_answers),
            "attempts": len(verdicts) + len(result.benign_answers),
        },
        "wall_seconds": round(result.wall_seconds, 1),
        "summaries": [summary_to_dict(s) for s in summarise(result)],
        "attacks": [
            {
                **verdict_to_dict(verdict),
                "answer": answer.text,
                "latency_ms": round(answer.latency_ms, 1),
            }
            for verdict, (answer, _attack) in zip(
                verdicts, result.attack_answers, strict=True
            )
        ],
        "benign": [
            {
                "question_id": question["id"],
                "condition": answer.condition,
                "grounded": quality.grounded,
                "expected_doc_seen": quality.expected_doc_seen,
                "matched_keywords": quality.matched_keywords,
            }
            for (answer, question), quality in zip(
                result.benign_answers, result.benign_quality, strict=True
            )
        ],
    }


def verdict_to_dict(verdict) -> dict:
    return {
        "attack_id": verdict.attack_id,
        "family": verdict.family,
        "surface": verdict.surface,
        "condition": verdict.condition,
        "success": verdict.success,
        "marker": verdict.marker,
        "blocked": verdict.blocked,
    }


def summary_to_dict(summary: ConditionSummary) -> dict:
    return {
        "condition": summary.condition,
        "attacks": summary.attacks,
        "successes": summary.successes,
        "success_rate": round(summary.success_rate, 4),
        "blocked": summary.blocked,
        "by_family": {
            family: {"attacks": bucket["attacks"], "successes": bucket["successes"]}
            for family, bucket in summary.by_family.items()
        },
        "by_marker": {
            marker: {"attacks": bucket["attacks"], "successes": bucket["successes"]}
            for marker, bucket in summary.by_marker.items()
        },
        "benign_questions": summary.benign_questions,
        "benign_grounded": summary.benign_grounded,
        "benign_grounded_rate": round(summary.benign_grounded_rate, 4),
        "median_latency_ms": round(summary.median_latency_ms, 1),
        "mean_prompt_tokens": round(summary.mean_prompt_tokens, 1),
        "mean_completion_tokens": round(summary.mean_completion_tokens, 1),
    }


def write_artifacts(result: RunResult, json_path: str, jsonl_path: str) -> dict:
    """Write the run summary and the per-attempt JSONL, and return the record."""

    import json
    from pathlib import Path

    record = build_record(result)
    for path in (json_path, jsonl_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, sort_keys=False)
        handle.write("\n")

    with open(jsonl_path, "w", encoding="utf-8", newline="\n") as handle:
        for verdict, (answer, attack) in zip(
            result.attack_verdicts, result.attack_answers, strict=True
        ):
            handle.write(
                json.dumps(
                    {
                        "model": result.model,
                        **attack_line(verdict, answer, attack),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        for (answer, question), quality in zip(
            result.benign_answers, result.benign_quality, strict=True
        ):
            handle.write(
                json.dumps(
                    {
                        "model": result.model,
                        **benign_line(answer, question, quality),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return record
