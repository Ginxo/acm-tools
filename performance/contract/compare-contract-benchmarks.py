#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Compare saved contract benchmark JSON files (before/after perf work)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def human_bytes(n: float) -> str:
    n = int(n)
    if n >= 1024**2:
        return f"{n / 1024**2:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct_delta(before: float, after: float) -> str:
    if before == 0:
        return "n/a"
    delta = (after - before) / before * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def md_cell(value: str) -> str:
    return value.replace("|", "\\|")


def format_elapsed(seconds: float) -> str:
    elapsed = float(seconds)
    if elapsed == 0:
        return "<1ms"
    if elapsed < 0.001:
        return f"{elapsed * 1000:.2f}ms"
    if elapsed < 1:
        return f"{elapsed * 1000:.1f}ms"
    return f"{elapsed:.3f}s"


def case_meta(doc: dict, case_id: str) -> dict:
    cases = (doc.get("summary") or {}).get("cases") or {}
    return cases.get(case_id) or {}


def case_runs(doc: dict, case_id: str) -> list[dict]:
    runs: list[dict] = []
    for run in doc.get("runs") or []:
        for case in run.get("cases") or []:
            if case.get("id") == case_id:
                runs.append(case)
                break
    return runs


def case_skipped(meta: dict, doc: dict, case_id: str) -> bool:
    skipped = meta.get("skipped")
    if isinstance(skipped, dict):
        return bool(skipped.get("median"))
    if isinstance(skipped, bool):
        return skipped
    runs = case_runs(doc, case_id)
    if runs:
        return any(bool(c.get("skipped")) for c in runs)
    return False


def case_success(meta: dict, doc: dict, case_id: str) -> bool:
    success = meta.get("success")
    if isinstance(success, dict):
        return bool(success.get("median"))
    if isinstance(success, bool):
        return success
    runs = case_runs(doc, case_id)
    if runs:
        return all(bool(c.get("success")) for c in runs)
    return False


def case_http_status(meta: dict, doc: dict, case_id: str) -> int:
    status = meta.get("http_status")
    if isinstance(status, dict):
        return int(status.get("median", 0))
    if isinstance(status, int):
        return status
    runs = case_runs(doc, case_id)
    if runs:
        return int(runs[0].get("http_status", 0))
    return 0


def case_response_bytes(meta: dict, doc: dict, case_id: str) -> int:
    bytes_ = meta.get("response_bytes")
    if isinstance(bytes_, dict):
        return int(bytes_.get("median", 0))
    if isinstance(bytes_, int):
        return bytes_
    runs = case_runs(doc, case_id)
    if runs:
        return int(runs[0].get("response_bytes", 0))
    return 0


def outcome_counts(doc: dict) -> dict[str, int]:
    summary_outcomes = (doc.get("summary") or {}).get("outcomes")
    if summary_outcomes:
        return summary_outcomes
    counts = {"ok": 0, "skip": 0, "fail": 0}
    cases = (doc.get("summary") or {}).get("cases") or {}
    if cases and any("success" in meta for meta in cases.values()):
        for case_id, meta in cases.items():
            if case_skipped(meta, doc, case_id):
                counts["skip"] += 1
            elif case_success(meta, doc, case_id):
                counts["ok"] += 1
            else:
                counts["fail"] += 1
        return counts
    runs = doc.get("runs") or []
    if not runs:
        return counts
    for case in runs[0].get("cases") or []:
        if case.get("skipped"):
            counts["skip"] += 1
        elif case.get("success"):
            counts["ok"] += 1
        else:
            counts["fail"] += 1
    return counts


def comparable_cases(before: dict, after: dict) -> list[str]:
    b_cases = (before.get("summary") or {}).get("cases") or {}
    a_cases = (after.get("summary") or {}).get("cases") or {}
    return sorted(set(b_cases) & set(a_cases))


def top_case_deltas_by_pct(
    before: dict, after: dict, top_n: int = 15
) -> list[tuple[str, float, float, float]]:
    deltas: list[tuple[str, float, float, float]] = []
    for case_id in comparable_cases(before, after):
        b_meta = case_meta(before, case_id)
        a_meta = case_meta(after, case_id)
        if case_skipped(b_meta, before, case_id) or case_skipped(a_meta, after, case_id):
            continue
        b_el = float(b_meta["elapsed_seconds"]["median"])
        a_el = float(a_meta["elapsed_seconds"]["median"])
        if b_el <= 0:
            continue
        delta_pct = (a_el - b_el) / b_el * 100
        deltas.append((case_id, b_el, a_el, delta_pct))
    deltas.sort(key=lambda item: abs(item[3]), reverse=True)
    return deltas[:top_n]


def top_case_deltas_by_abs(
    before: dict, after: dict, top_n: int = 15
) -> list[tuple[str, float, float, float]]:
    deltas: list[tuple[str, float, float, float]] = []
    for case_id in comparable_cases(before, after):
        b_meta = case_meta(before, case_id)
        a_meta = case_meta(after, case_id)
        if case_skipped(b_meta, before, case_id) or case_skipped(a_meta, after, case_id):
            continue
        b_el = float(b_meta["elapsed_seconds"]["median"])
        a_el = float(a_meta["elapsed_seconds"]["median"])
        delta_abs = a_el - b_el
        deltas.append((case_id, b_el, a_el, delta_abs))
    deltas.sort(key=lambda item: abs(item[3]), reverse=True)
    return deltas[:top_n]


def outlier_cases(after: dict, threshold: float = 3.0) -> list[tuple[str, float, float, float]]:
    outliers: list[tuple[str, float, float, float]] = []
    cases = (after.get("summary") or {}).get("cases") or {}
    for case_id, meta in sorted(cases.items()):
        if case_skipped(meta, after, case_id):
            continue
        stats = meta.get("elapsed_seconds") or {}
        median = float(stats.get("median", 0))
        max_el = float(stats.get("max", 0))
        if median <= 0 or max_el <= median * threshold:
            continue
        outliers.append((case_id, median, max_el, max_el / median))
    outliers.sort(key=lambda item: item[3], reverse=True)
    return outliers[:10]


def print_summary_text(label: str, doc: dict) -> None:
    summary = doc.get("summary") or {}
    total = summary.get("total_elapsed_seconds", {})
    process = summary.get("process", {})
    cpu_avg = process.get("cpu_percent", {}).get("avg", {})
    rss_peak = process.get("memory_rss_peak_bytes", {})
    outcomes = summary.get("outcomes") or outcome_counts(doc)
    print(f"=== {label} ===")
    print(f"  label:     {doc.get('label', '?')}")
    print(f"  backend:   {doc.get('backend_url', '?')}")
    print(f"  timestamp: {doc.get('timestamp', '?')}")
    if doc.get("console_git"):
        print(f"  console:   {doc['console_git']}")
    if doc.get("notes"):
        print(f"  notes:     {doc['notes']}")
    print(f"  cases:     {doc.get('case_count', '?')}")
    print(f"  outcomes:  ok={outcomes.get('ok', 0)} skip={outcomes.get('skip', 0)} fail={outcomes.get('fail', 0)}")
    print(
        f"  total:     min={total.get('min')}s  avg={total.get('avg')}s  "
        f"median={total.get('median')}s  max={total.get('max')}s"
    )
    print(
        f"  cpu avg:   min={cpu_avg.get('min')}%  avg={cpu_avg.get('avg')}%  "
        f"median={cpu_avg.get('median')}%  max={cpu_avg.get('max')}%"
    )
    rss_avg = int(rss_peak.get("avg", 0))
    print(
        f"  rss peak:  avg={human_bytes(rss_avg)}  median={human_bytes(int(rss_peak.get('median', 0)))}"
    )
    print()


def compare_docs_text(before: dict, after: dict, top_n: int) -> None:
    print_summary_text("Before", before)
    print_summary_text("After", after)

    b = before["summary"]
    a = after["summary"]
    b_total = b["total_elapsed_seconds"]["median"]
    a_total = a["total_elapsed_seconds"]["median"]
    b_cpu = b["process"]["cpu_percent"]["avg"]["median"]
    a_cpu = a["process"]["cpu_percent"]["avg"]["median"]
    b_rss = b["process"]["memory_rss_peak_bytes"]["median"]
    a_rss = a["process"]["memory_rss_peak_bytes"]["median"]

    print("=== Delta (after vs before) ===")
    print(f"  total elapsed median: {a_total}s  ({pct_delta(b_total, a_total)} — negative is faster)")
    print(f"  cpu avg median:       {a_cpu}%  ({pct_delta(b_cpu, a_cpu)})")
    print(f"  rss peak median:      {human_bytes(a_rss)}  ({pct_delta(b_rss, a_rss)})")
    print()

    print(f"=== Top {top_n} case deltas by |abs| (median elapsed, skips excluded) ===")
    for case_id, b_el, a_el, delta_abs in top_case_deltas_by_abs(before, after, top_n):
        sign = "+" if delta_abs > 0 else ""
        print(
            f"  {case_id:40s}  {format_elapsed(b_el)} -> {format_elapsed(a_el)}  "
            f"({sign}{delta_abs:.3f}s)"
        )


def compare_docs_markdown(
    before: dict,
    after: dict,
    top_n: int,
    before_path: str,
    after_path: str,
) -> str:
    b = before["summary"]
    a = after["summary"]
    b_total = float(b["total_elapsed_seconds"]["median"])
    a_total = float(a["total_elapsed_seconds"]["median"])
    b_cpu = float(b["process"]["cpu_percent"]["avg"]["median"])
    a_cpu = float(a["process"]["cpu_percent"]["avg"]["median"])
    b_rss = float(b["process"]["memory_rss_peak_bytes"]["median"])
    a_rss = float(a["process"]["memory_rss_peak_bytes"]["median"])
    b_outcomes = b.get("outcomes") or outcome_counts(before)
    a_outcomes = a.get("outcomes") or outcome_counts(after)

    lines = [
        "## Contract performance comparison",
        "",
        f"**Before:** `{before_path}`  ",
        f"**After:** `{after_path}`",
        "",
        "### Run metadata",
        "",
        "| | Before | After |",
        "| --- | --- | --- |",
        f"| Label | {md_cell(str(before.get('label', '?')))} | {md_cell(str(after.get('label', '?')))} |",
        f"| Backend | {md_cell(str(before.get('backend_url', '?')))} | {md_cell(str(after.get('backend_url', '?')))} |",
        f"| Timestamp | {md_cell(str(before.get('timestamp', '?')))} | {md_cell(str(after.get('timestamp', '?')))} |",
        f"| Console git | {md_cell(str(before.get('console_git') or '—'))} | {md_cell(str(after.get('console_git') or '—'))} |",
        f"| Notes | {md_cell(str(before.get('notes') or '—'))} | {md_cell(str(after.get('notes') or '—'))} |",
        f"| Cases | {before.get('case_count', '?')} | {after.get('case_count', '?')} |",
        "",
        "### Outcomes (median across runs)",
        "",
        "| | Before | After |",
        "| --- | ---: | ---: |",
        f"| ok | {b_outcomes.get('ok', 0)} | {a_outcomes.get('ok', 0)} |",
        f"| skip | {b_outcomes.get('skip', 0)} | {a_outcomes.get('skip', 0)} |",
        f"| fail | {b_outcomes.get('fail', 0)} | {a_outcomes.get('fail', 0)} |",
        "",
        "### Summary (median across runs)",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
        f"| Total elapsed | {format_elapsed(b_total)} | {format_elapsed(a_total)} | {pct_delta(b_total, a_total)} |",
        f"| CPU avg | {b_cpu:.1f}% | {a_cpu:.1f}% | {pct_delta(b_cpu, a_cpu)} |",
        f"| RSS peak | {human_bytes(b_rss)} | {human_bytes(a_rss)} | {pct_delta(b_rss, a_rss)} |",
        "",
        "> **Delta:** negative % on elapsed = faster; negative % on CPU/RSS = lower usage.",
        "",
        f"### Top {top_n} case deltas (by |abs| change, median elapsed, skips excluded)",
        "",
        "| Case | Before | After | Δ abs | ok/skip | status | bytes |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]

    abs_rows = top_case_deltas_by_abs(before, after, top_n)
    if not abs_rows:
        lines.append("| _no comparable cases_ | | | | | | |")
    for case_id, b_el, a_el, delta_abs in abs_rows:
        a_meta = case_meta(after, case_id)
        sign = "+" if delta_abs > 0 else ""
        outcome = (
            "skip"
            if case_skipped(a_meta, after, case_id)
            else ("ok" if case_success(a_meta, after, case_id) else "fail")
        )
        lines.append(
            f"| `{case_id}` | {format_elapsed(b_el)} | {format_elapsed(a_el)} | "
            f"{sign}{delta_abs:.3f}s | {outcome} | {case_http_status(a_meta, after, case_id)} | "
            f"{human_bytes(case_response_bytes(a_meta, after, case_id))} |"
        )

    pct_rows = top_case_deltas_by_pct(before, after, top_n)
    lines.extend(
        [
            "",
            f"### Top {top_n} case deltas (by |%| change, median elapsed, skips excluded)",
            "",
            "| Case | Before | After | Δ % |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    if not pct_rows:
        lines.append("| _no comparable cases_ | | | |")
    for case_id, b_el, a_el, delta_pct in pct_rows:
        sign = "+" if delta_pct > 0 else ""
        lines.append(
            f"| `{case_id}` | {format_elapsed(b_el)} | {format_elapsed(a_el)} | {sign}{delta_pct:.1f}% |"
        )

    outliers = outlier_cases(after)
    if outliers:
        lines.extend(
            [
                "",
                "### After-run outliers (max / median ≥ 3×)",
                "",
                "| Case | Median | Max | Ratio |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for case_id, median, max_el, ratio in outliers:
            lines.append(
                f"| `{case_id}` | {format_elapsed(median)} | {format_elapsed(max_el)} | {ratio:.1f}× |"
            )

    lines.extend(
        [
            "",
            "### Full run stats",
            "",
            "| Metric | Before min | Before median | Before max | After min | After median | After max |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    def row(metric: str, b_stats: dict, a_stats: dict, suffix: str = "") -> str:
        return (
            f"| {metric} | {b_stats.get('min')}{suffix} | {b_stats.get('median')}{suffix} | "
            f"{b_stats.get('max')}{suffix} | {a_stats.get('min')}{suffix} | "
            f"{a_stats.get('median')}{suffix} | {a_stats.get('max')}{suffix} |"
        )

    lines.append(row("Total elapsed", b["total_elapsed_seconds"], a["total_elapsed_seconds"], "s"))
    lines.append(
        row(
            "CPU avg",
            b["process"]["cpu_percent"]["avg"],
            a["process"]["cpu_percent"]["avg"],
            "%",
        )
    )
    b_rss_stats = b["process"]["memory_rss_peak_bytes"]
    a_rss_stats = a["process"]["memory_rss_peak_bytes"]
    lines.append(
        "| RSS peak | "
        f"{human_bytes(b_rss_stats.get('min', 0))} | {human_bytes(b_rss_stats.get('median', 0))} | "
        f"{human_bytes(b_rss_stats.get('max', 0))} | "
        f"{human_bytes(a_rss_stats.get('min', 0))} | {human_bytes(a_rss_stats.get('median', 0))} | "
        f"{human_bytes(a_rss_stats.get('max', 0))} |"
    )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare contract benchmark JSON files")
    parser.add_argument("before", help="Baseline benchmark JSON")
    parser.add_argument("after", help="After-change benchmark JSON")
    parser.add_argument("--top", type=int, default=15, help="Top N cases by delta")
    parser.add_argument(
        "--format",
        choices=("markdown", "text"),
        default="markdown",
        help="Output format (default: markdown for GitHub)",
    )
    args = parser.parse_args()

    try:
        before = load(args.before)
        after = load(args.after)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "markdown":
        print(
            compare_docs_markdown(
                before,
                after,
                args.top,
                args.before,
                args.after,
            )
        )
    else:
        compare_docs_text(before, after, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
