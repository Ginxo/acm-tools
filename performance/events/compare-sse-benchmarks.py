#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Compare saved SSE benchmark JSON files (before/after perf work)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median


def human_bytes(n: float) -> str:
    n = int(n)
    if n >= 1024**2:
        return f"{n / 1024**2:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "summary" not in data and "runs" in data:
        data["summary"] = summarize_runs(data["runs"])
    return data


def summarize_runs(runs: list[dict]) -> dict:
    loaded = [bool(r.get("loaded")) for r in runs]
    elapsed = [float(r["elapsed_seconds"]) for r in runs]
    decomp = [int(r["decompressed_bytes"]) for r in runs]
    events = [int(r.get("event_blocks_estimate", 0)) for r in runs]
    return {
        "runs": len(runs),
        "loaded_all": all(loaded),
        "elapsed_seconds": {
            "min": round(min(elapsed), 2),
            "max": round(max(elapsed), 2),
            "avg": round(mean(elapsed), 2),
            "median": round(median(elapsed), 2),
        },
        "decompressed_bytes": {
            "min": min(decomp),
            "max": max(decomp),
            "avg": round(mean(decomp)),
            "median": round(median(decomp)),
        },
        "event_blocks_estimate": {
            "min": min(events),
            "max": max(events),
            "avg": round(mean(events)),
            "median": round(median(events)),
        },
    }


def pct_delta(before: float, after: float) -> str:
    if before == 0:
        return "n/a"
    delta = (after - before) / before * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def print_summary(label: str, doc: dict) -> None:
    summary = doc.get("summary") or {}
    elapsed = summary.get("elapsed_seconds", {})
    decomp = summary.get("decompressed_bytes", {})
    events = summary.get("event_blocks_estimate", {})
    print(f"=== {label} ===")
    print(f"  label:     {doc.get('label', '?')}")
    print(f"  mode:      {doc.get('mode', '?')}")
    print(f"  timestamp: {doc.get('timestamp', '?')}")
    if doc.get("console_git"):
        print(f"  console:   {doc['console_git']}")
    if doc.get("notes"):
        print(f"  notes:     {doc['notes']}")
    print(f"  runs:      {summary.get('runs', '?')}  LOADED all: {summary.get('loaded_all', '?')}")
    print(
        f"  elapsed:   min={elapsed.get('min')}s  avg={elapsed.get('avg')}s  "
        f"median={elapsed.get('median')}s  max={elapsed.get('max')}s"
    )
    avg_decomp = int(decomp.get("avg", 0))
    print(
        f"  size:      avg={avg_decomp} B ({human_bytes(avg_decomp)})  "
        f"median={decomp.get('median')} B"
    )
    print(f"  events~:   avg={events.get('avg')}  median={events.get('median')}")
    print()


def compare_docs(before: dict, after: dict) -> None:
    print_summary("Before", before)
    print_summary("After", after)
    b = before["summary"]
    a = after["summary"]
    b_el = b["elapsed_seconds"]["avg"]
    a_el = a["elapsed_seconds"]["avg"]
    b_sz = b["decompressed_bytes"]["avg"]
    a_sz = a["decompressed_bytes"]["avg"]
    b_ev = b["event_blocks_estimate"]["avg"]
    a_ev = a["event_blocks_estimate"]["avg"]
    print("=== Delta (after vs before) ===")
    print(f"  elapsed avg:      {a_el}s  ({pct_delta(b_el, a_el)} — negative is faster)")
    print(f"  decompressed avg:   {human_bytes(a_sz)}  ({pct_delta(b_sz, a_sz)})")
    print(f"  event blocks avg:   {a_ev}  ({pct_delta(b_ev, a_ev)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare SSE benchmark JSON files")
    parser.add_argument("before", help="Baseline benchmark JSON")
    parser.add_argument("after", help="After-change benchmark JSON")
    args = parser.parse_args()

    try:
        before = load(args.before)
        after = load(args.after)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    compare_docs(before, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
