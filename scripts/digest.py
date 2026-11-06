#!/usr/bin/env python
"""Read results/audit.json and write results/digest.txt.

The digest is the per-family, per-marker, per-surface and latency breakdown of
one measured run, written out so the numbers quoted in README.md can be checked
against the artifact without reading 34 KB of JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _count(attacks: list[dict], **filters: object) -> int:
    return sum(1 for a in attacks if all(a.get(k) == v for k, v in filters.items()))


def _cell(attacks: list[dict], condition: str, **filters: object) -> str:
    total = _count(attacks, condition=condition, **filters)
    hit = _count(attacks, condition=condition, success=True, **filters)
    return f"{hit}/{total}"


def build_digest(record: dict) -> str:
    conditions = record["conditions"]
    attacks = record["attacks"]
    benign = record["benign"]
    lines: list[str] = []

    lines.append("prompt-injection-rag-audit digest")
    lines.append(f"model={record['model']} embedder={record['embedder']}")
    lines.append(f"wall_seconds={record['wall_seconds']} counts={record['counts']}")
    lines.append("")
    lines.append("== per condition ==")
    for s in record["summaries"]:
        lines.append(
            f"{s['condition']:>14}  attacks {s['successes']}/{s['attacks']} "
            f"succeeded ({s['success_rate'] * 100:.0f}%)  blocked {s['blocked']}  "
            f"benign grounded {s['benign_grounded']}/{s['benign_questions']} "
            f"({s['benign_grounded_rate'] * 100:.1f}%)  "
            f"median {s['median_latency_ms']:.0f} ms  "
            f"prompt tok {s['mean_prompt_tokens']:.1f}  "
            f"completion tok {s['mean_completion_tokens']:.1f}"
        )
    lines.append("")
    lines.append("== successes by family x condition ==")
    for family in sorted({a["family"] for a in attacks}):
        cells = [_cell(attacks, c, family=family) for c in conditions]
        lines.append(f"{family:>20}  " + "   ".join(cells))
    lines.append("")
    lines.append("== successes by marker x condition ==")
    for marker in record["success_markers"]:
        cells = [_cell(attacks, c, marker=marker) for c in conditions]
        lines.append(f"{marker:>20}  " + "   ".join(cells))
    lines.append("")
    lines.append("== successes by surface x condition ==")
    for surface in sorted({a["surface"] for a in attacks}):
        cells = [_cell(attacks, c, surface=surface) for c in conditions]
        lines.append(f"{surface:>20}  " + "   ".join(cells))
    lines.append("")
    lines.append("== blocked attempts ==")
    blocked = [a for a in attacks if a["blocked"]]
    for a in blocked:
        lines.append(f"{a['attack_id']:>22}  {a['condition']:>14}  {a['family']}  {a['marker']}")
    lines.append(f"total blocked={len(blocked)}")
    for condition in conditions:
        lines.append("")
        lines.append(f"== successful attacks (condition {condition}) ==")
        hits = [a for a in attacks if a["condition"] == condition and a["success"]]
        for a in hits:
            lines.append(
                f"{a['attack_id']:>22}  {a['surface']:>9}  {a['family']:>20}  {a['marker']}"
            )
        if not hits:
            lines.append("  none")
    lines.append("")
    lines.append("== benign answers that were not grounded ==")
    misses = [b for b in benign if not b["grounded"]]
    for b in misses:
        lines.append(
            f"{b['question_id']:>22}  {b['condition']:>14}  "
            f"expected_doc_seen={b['expected_doc_seen']}  matched={b['matched_keywords']}"
        )
    if not misses:
        lines.append("  none")
    lines.append("")
    lines.append("== benign answers by condition ==")
    for condition in conditions:
        rows = [b for b in benign if b["condition"] == condition]
        grounded = sum(1 for b in rows if b["grounded"])
        seen = sum(1 for b in rows if b["expected_doc_seen"])
        lines.append(
            f"{condition:>14}  grounded {grounded}/{len(rows)}  "
            f"expected doc retrieved {seen}/{len(rows)}"
        )
    lines.append("")
    lines.append("== latency spread per condition, attacks only ==")
    for condition in conditions:
        values = sorted(a["latency_ms"] for a in attacks if a["condition"] == condition)
        if values:
            lines.append(
                f"{condition:>14}  min {values[0]:.0f} ms  "
                f"p25 {values[len(values) // 4]:.0f} ms  "
                f"median {values[len(values) // 2]:.0f} ms  "
                f"max {values[-1]:.0f} ms"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the measured-run digest")
    parser.add_argument("--record", default="results/audit.json")
    parser.add_argument("--out", default="results/digest.txt")
    args = parser.parse_args()

    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    text = build_digest(record)
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
