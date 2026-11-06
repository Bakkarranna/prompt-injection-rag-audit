#!/usr/bin/env python
"""Run the audit and write the measured artifacts.

Usage:
    python scripts/run_audit.py [--out-dir results] [--data-dir data]

A full run over 200 attacks and 40 questions takes hours, so it is journalled
as it goes into <out-dir>/audit.journal.jsonl. If the process stops, run the
same command again: every attempt already in the journal is replayed and only
the missing ones are asked of the model. The journal carries the model that
answered, so switching models means starting from a fresh journal.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragaudit.config import load_settings  # noqa: E402
from ragaudit.evaluate import run_audit  # noqa: E402
from ragaudit.journal import (  # noqa: E402
    close_line,
    done_keys,
    known_wall_seconds,
    merge,
    meta_line,
    read_lines,
    rebuild,
)
from ragaudit.report import write_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the prompt-injection RAG audit")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print per-attempt progress"
    )
    args = parser.parse_args()

    settings = load_settings()
    journal_path = Path(args.out_dir) / "audit.journal.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    lines = read_lines(journal_path)
    journalled_models = {
        line["model"] for line in lines if line.get("kind") == "meta"
    }
    if journalled_models and journalled_models != {settings.ollama_model}:
        print(
            f"the journal in {journal_path} was started for {sorted(journalled_models)}, "
            f"not {settings.ollama_model}. Move it aside to run a fresh audit.",
            flush=True,
        )
        return 1

    done = done_keys(lines)
    replayed = rebuild(
        lines,
        settings,
        model=settings.ollama_model,
        wall_seconds=known_wall_seconds(lines),
    )
    if done:
        print(
            f"resuming: {len(done)} attempts already journalled, "
            f"{replayed.wall_seconds:.0f} s of recorded attempt and completed-segment time",
            flush=True,
        )

    progress = None if args.quiet else (lambda message: print(message, flush=True))

    with open(journal_path, "a", encoding="utf-8", newline="\n") as journal_handle:

        def on_attempt(line: dict) -> None:
            journal_handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            journal_handle.flush()

        started = time.time()
        on_attempt(meta_line(started, settings.ollama_model))
        fresh = run_audit(
            settings=settings,
            data_dir=args.data_dir,
            progress=progress,
            done=done,
            on_attempt=on_attempt,
        )
        result = merge(replayed, fresh)
        on_attempt(close_line(time.time() - started))

    json_path = str(Path(args.out_dir) / "audit.json")
    jsonl_path = str(Path(args.out_dir) / "audit.jsonl")
    record = write_artifacts(result, json_path, jsonl_path)

    print()
    for summary in record["summaries"]:
        print(
            f"{summary['condition']:<16} "
            f"{summary['successes']:>2}/{summary['attacks']} attacks succeeded "
            f"({summary['success_rate'] * 100:.0f}%), "
            f"{summary['blocked']} blocked, "
            f"benign grounded {summary['benign_grounded']}/"
            f"{summary['benign_questions']}, "
            f"median {summary['median_latency_ms']:.0f} ms"
        )
    print()
    print(f"wrote {json_path}")
    print(f"wrote {jsonl_path}")
    print(
        f"wall time {record['wall_seconds']} s over {record['counts']['attempts']} attempts "
        f"({len(done)} replayed from the journal)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
