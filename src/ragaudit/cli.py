"""Command-line entry point, mirroring scripts/run_audit.py for installed use."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the prompt-injection RAG audit")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print per-attempt progress"
    )
    args = parser.parse_args()

    from ragaudit.evaluate import run_audit
    from ragaudit.report import write_artifacts

    progress = None if args.quiet else (lambda message: print(message, flush=True))
    result = run_audit(data_dir=args.data_dir, progress=progress)

    from pathlib import Path

    json_path = str(Path(args.out_dir) / "audit.json")
    jsonl_path = str(Path(args.out_dir) / "audit.jsonl")
    for path in (json_path, jsonl_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
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
    print(f"wall time {record['wall_seconds']} s over {record['counts']['attempts']} attempts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
