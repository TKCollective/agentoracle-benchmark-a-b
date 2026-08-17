#!/usr/bin/env python3
"""Daily results page — cumulative counts only.

The pre-registration allows exactly two content moments: the pre-registration
and the results. Between them, this page publishes **cumulative counts only —
no interpretation, no interim conclusions, no early signal**
(``docs/pre-registration.md``, "The commitment").

Therefore this module, by construction, emits only these integers:

* questions run
* citations emitted
* deterministic labels issued
* receipts published

It computes **no rates, no percentages, no per-arm or per-model comparisons and
no differences**. Counts are not split by arm, because a per-arm split of
emitted citations is itself an interim comparison. A guard at the end of the
module refuses to write output containing forbidden tokens (``%``, "rate",
"survival", "versus", "improve", ...), so an accidental future edit that leaks
signal fails loudly instead of publishing quietly.

Usage::

    python3 harness/results_page/publish_daily.py --data-dir data --out results/daily.md

MIT licensed, part of the Experiment A harness.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from typing import Any, Dict, Iterable, List

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Tokens that would constitute interpretation or early signal.
FORBIDDEN = [
    "rate", "rates", "ratio", "percent", "percentage", "survival", "precision",
    "recall", "versus", "vs", "compare", "comparison", "better", "worse",
    "improve", "improvement", "increase", "decrease", "trend", "conclude",
    "conclusion", "signal", "outperform", "delta", "difference",
]
# Matched on whole words only, so ordinary prose ("pre-registration") does not
# trip the guard while "rate" or "survival" would.


class EarlySignalError(RuntimeError):
    """Raised if generated output would leak interpretation or early signal."""


def iter_jsonl(paths: Iterable[pathlib.Path]) -> Iterable[Dict[str, Any]]:
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if line.strip():
                yield json.loads(line)


def count_raw(data_dir: pathlib.Path) -> Dict[str, int]:
    """Cumulative counts over all raw result files in ``data_dir``."""
    questions = 0
    emitted = 0
    files = sorted(p for p in data_dir.glob("experiment-a-*.jsonl") if ".archived-" not in p.name)
    for rec in iter_jsonl(files):
        questions += 1
        for arm in rec.get("arms", []):
            emitted += int(arm.get("counts", {}).get("citations_emitted", 0))
    return {"questions_run": questions, "citations_emitted": emitted, "raw_files": len(files)}


def count_labels(data_dir: pathlib.Path) -> int:
    """Deterministic labels issued by the independent ground-truth stack.

    Labels live in ``data/labels/*.jsonl`` and are produced outside this harness
    (``docs/ground-truth-spec.md``). Only their number is reported here.
    """
    label_dir = data_dir / "labels"
    if not label_dir.exists():
        return 0
    return sum(1 for _ in iter_jsonl(sorted(label_dir.glob("*.jsonl"))))


def count_receipts(data_dir: pathlib.Path) -> int:
    receipt_dir = data_dir / "receipts"
    if not receipt_dir.exists():
        return 0
    return sum(1 for _ in iter_jsonl(sorted(receipt_dir.glob("receipts-*.jsonl"))))


def build_counts(data_dir: pathlib.Path) -> Dict[str, int]:
    raw = count_raw(data_dir)
    return {
        "questions_run": raw["questions_run"],
        "citations_emitted": raw["citations_emitted"],
        "deterministic_labels_issued": count_labels(data_dir),
        "receipts_published": count_receipts(data_dir),
    }


def render(counts: Dict[str, int], stamp: str) -> str:
    lines = [
        "# Experiment A — cumulative counts",
        "",
        f"Updated {stamp}.",
        "",
        "Counts only. Per the pre-registration, no interpretation is published",
        "before the pre-registered results date.",
        "",
        "| Quantity | Count |",
        "|---|---|",
        f"| Questions run | {counts['questions_run']} |",
        f"| Citations emitted | {counts['citations_emitted']} |",
        f"| Deterministic labels issued | {counts['deterministic_labels_issued']} |",
        f"| Receipts published | {counts['receipts_published']} |",
        "",
        "Design: [`docs/pre-registration.md`](../docs/pre-registration.md).",
        "Ground truth: [`docs/ground-truth-spec.md`](../docs/ground-truth-spec.md).",
        "",
    ]
    return "\n".join(lines)


def guard(text: str) -> None:
    body = text.lower()
    words = set(re.findall(r"[a-z]+", body))
    hits = sorted(words & set(FORBIDDEN))
    if "%" in body:
        hits.append("%")
    if hits:
        raise EarlySignalError(
            "refusing to publish: output contains forbidden interpretation token(s): "
            + ", ".join(hits)
        )
    if re.search(r"\d+\s*(?:%|per\s*cent)", body):
        raise EarlySignalError("refusing to publish: output contains a percentage")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish cumulative counts only.")
    p.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    p.add_argument("--out", default=str(REPO_ROOT / "results" / "daily.md"))
    p.add_argument("--json-out", default="", help="optional path for the counts as JSON")
    p.add_argument("--stdout", action="store_true", help="print instead of writing files")
    args = p.parse_args(argv)

    data_dir = pathlib.Path(args.data_dir)
    counts = build_counts(data_dir)
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    page = render(counts, stamp)
    guard(page)

    if args.stdout:
        print(page)
        print(json.dumps(counts, indent=2, sort_keys=True))
        return 0

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    if args.json_out:
        jp = pathlib.Path(args.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps({"updated": stamp, "counts": counts}, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
