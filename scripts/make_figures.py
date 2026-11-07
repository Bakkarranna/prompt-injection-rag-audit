#!/usr/bin/env python
"""Regenerate the report figures from results/audit.json.

Three figures, all read from the measured artifact and all sized from it, so the
same script draws the 20-attack record and the scaled one without an edit:

* attacks that succeeded under each defence condition
* successes by attack family and condition
* latency of the attack attempts by condition, with the median marked

Usage:
    python scripts/make_figures.py [--record results/audit.json] [--out-dir docs/figures]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

INK = "#1f2933"
MUTED = "#6b7684"
ACCENT = "#1f6f8b"
ACCENT_SOFT = "#7fb3c4"
GRID = "#d7dde5"


def _style(ax) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.label.set_color(MUTED)
    ax.xaxis.label.set_color(MUTED)


def _record(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ceil_limit(value: int) -> int:
    """An axis limit just above `value`, so the annotations clear the bars."""
    if value <= 0:
        return 1
    step = 10 ** (len(str(value)) - 1)
    return ((value // step) + 2) * step


def success_by_condition(record: dict, out_dir: Path) -> Path:
    summaries = record["summaries"]
    conditions = [s["condition"] for s in summaries]
    successes = [s["successes"] for s in summaries]
    total = summaries[0]["attacks"]
    conditions_total = {s["attacks"] for s in summaries}
    if conditions_total != {total}:
        raise ValueError(f"inconsistent attack totals across conditions: {conditions_total}")

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    bars = ax.bar(conditions, successes, width=0.55, color=[ACCENT, ACCENT_SOFT, ACCENT_SOFT])
    for bar, value in zip(bars, successes, strict=True):
        ax.annotate(
            f"{value} of {total}",
            (bar.get_x() + bar.get_width() / 2, value),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=9,
            color=INK,
        )
    ax.set_ylim(0, _ceil_limit(max(successes)))
    ax.set_ylabel("attacks that succeeded")
    ax.set_title("Attack success by defence condition", color=INK, fontsize=11)
    _style(ax)
    fig.tight_layout()
    path = out_dir / "attack_success_by_condition.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def success_by_family(record: dict, out_dir: Path) -> Path:
    attacks = record["attacks"]
    conditions = record["conditions"]
    families = sorted({a["family"] for a in attacks})
    per_family = max(sum(1 for a in attacks if a["family"] == family) for family in families)
    per_condition = per_family // len(conditions)

    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    width = 0.26
    highest = 0
    for index, condition in enumerate(conditions):
        values = [
            sum(
                1
                for a in attacks
                if a["family"] == family
                and a["condition"] == condition
                and a["success"]
            )
            for family in families
        ]
        highest = max(highest, max(values))
        offsets = [i + (index - 1) * width for i in range(len(families))]
        colour = ACCENT if index == 0 else ACCENT_SOFT
        ax.bar(offsets, values, width=width, label=condition, color=colour)
    ax.set_xticks(range(len(families)))
    ax.set_xticklabels([f.replace("_", "\n") for f in families], fontsize=8)
    ax.set_ylim(0, _ceil_limit(highest))
    ax.set_ylabel(f"attacks that succeeded (of {per_condition})")
    ax.set_title("Successes by attack family and condition", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=8, ncols=3, loc="upper center")
    _style(ax)
    fig.tight_layout()
    path = out_dir / "success_by_family.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def latency_by_condition(record: dict, out_dir: Path) -> Path:
    attacks = record["attacks"]
    conditions = record["conditions"]
    per_condition = max(
        sum(1 for a in attacks if a["condition"] == condition) for condition in conditions
    )

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for index, condition in enumerate(conditions):
        values = [a["latency_ms"] for a in attacks if a["condition"] == condition]
        values.sort()
        median = values[len(values) // 2]
        ax.plot([index] * len(values), values, "o", ms=3.5, color=ACCENT_SOFT, alpha=0.9)
        ax.plot([index - 0.18, index + 0.18], [median, median], color=ACCENT, linewidth=2)
        ax.annotate(
            f"median {median:.0f} ms",
            (index, median),
            textcoords="offset points",
            xytext=(10, -3),
            fontsize=8,
            color=INK,
        )
    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels(conditions)
    ax.set_xlim(-0.6, len(conditions) - 0.4)
    ax.set_ylabel("latency (ms)")
    ax.set_title(f"Latency of the {per_condition} attack attempts", color=INK, fontsize=11)
    _style(ax)
    fig.tight_layout()
    path = out_dir / "latency_by_condition.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the report figures")
    parser.add_argument("--record", default="results/audit.json")
    parser.add_argument("--out-dir", default="docs/figures")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = _record(args.record)
    for figure in (
        success_by_condition,
        success_by_family,
        latency_by_condition,
    ):
        written = figure(record, out_dir)
        print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
