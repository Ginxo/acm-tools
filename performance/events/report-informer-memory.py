#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Estimate console backend informer-cache memory by WatchSpec.

Lists hub objects with the same label/field selectors as
backend/internal/informers/specs.go DefaultWatchSpecs(), estimates JSON
bytes after stripping managedFields (except Policy), and ranks kinds for
useFleetSearch migration candidates.

Outputs:
  generated/informer-memory.md
  generated/informer-memory.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watch_specs import WATCH_SPECS, WatchSpec, validate_specs

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = SCRIPT_DIR / "generated"


@dataclass
class SpecReport:
    spec: WatchSpec
    status: str = "ok"  # ok | not-installed | error
    error: str = ""
    count: int = 0
    bytes_total: int = 0
    bytes_max: int = 0
    bytes_p50: int = 0
    search_count: int | None = None
    sample_names: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return self.spec.mode

    @property
    def hint(self) -> str:
        return self.spec.migration_hint()


def run_oc(args: list[str], *, timeout: int = 300) -> tuple[int, str, str]:
    result = subprocess.run(
        ["oc", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024**3:
        return f"{n / 1024**2:.2f} MiB"
    return f"{n / 1024**3:.2f} GiB"


def strip_for_cache(obj: dict[str, Any], keep_managed_fields: bool) -> dict[str, Any]:
    """Approximate informer transform (DeepCopy + strip managedFields except Policy)."""
    # Shallow structural copy via JSON round-trip for size estimate.
    clone = json.loads(json.dumps(obj))
    if not keep_managed_fields:
        meta = clone.get("metadata")
        if isinstance(meta, dict) and "managedFields" in meta:
            del meta["managedFields"]
    return clone


def object_bytes(obj: dict[str, Any], keep_managed_fields: bool) -> int:
    cleaned = strip_for_cache(obj, keep_managed_fields)
    return len(json.dumps(cleaned, separators=(",", ":")).encode("utf-8"))


def percentile(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = int(round((len(sorted_vals) - 1) * p))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


class ApiResourceCache:
    def __init__(self) -> None:
        self._by_kind: dict[tuple[str, str], dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        rc, out, err = run_oc(["api-resources", "-o", "json"], timeout=120)
        if rc != 0:
            # Older oc may lack -o json; fall back to wide text parse.
            rc2, out2, err2 = run_oc(["api-resources"], timeout=120)
            if rc2 != 0:
                raise RuntimeError(f"oc api-resources failed: {err or err2}")
            self._parse_text(out2)
        else:
            data = json.loads(out)
            for item in data.get("resources", data if isinstance(data, list) else []):
                # oc api-resources -o json shape varies by version.
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind") or item.get("Kind")
                name = item.get("name") or item.get("Name")
                group = item.get("group") or item.get("Group") or ""
                version = item.get("version") or item.get("Version") or ""
                namespaced = item.get("namespaced", item.get("Namespaced", True))
                if not kind or not name:
                    continue
                # Prefer non-subresource (no / in name)
                if "/" in name:
                    continue
                api_version = f"{group}/{version}" if group else version
                key = (api_version, kind)
                # First wins; also index by kind+group alone for fuzzy match
                if key not in self._by_kind:
                    self._by_kind[key] = {
                        "name": name,
                        "namespaced": bool(namespaced),
                        "group": group,
                        "version": version,
                        "apiVersion": api_version,
                    }
        self._loaded = True

    def _parse_text(self, text: str) -> None:
        lines = text.splitlines()
        if not lines:
            return
        # Skip header
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            # NAME SHORTNAMES APIVERSION NAMESPACED KIND  (columns vary)
            name = parts[0]
            if "/" in name:
                continue
            # Find NAMESPACED bool and KIND (last column usually)
            kind = parts[-1]
            namespaced = "true" in parts[-2].lower() if len(parts) >= 2 else True
            # APIVERSION is usually 3rd or contains /
            api_version = ""
            for p in parts[1:-2]:
                if "/" in p or p in {"v1", "v1beta1", "v1alpha1", "v1beta2"}:
                    api_version = p
                    break
            if not api_version:
                continue
            key = (api_version, kind)
            if key not in self._by_kind:
                self._by_kind[key] = {
                    "name": name,
                    "namespaced": namespaced,
                    "apiVersion": api_version,
                }

    def resolve(self, spec: WatchSpec) -> dict[str, Any] | None:
        self.load()
        key = (spec.api_version, spec.kind)
        if key in self._by_kind:
            return self._by_kind[key]
        # Fuzzy: match kind + group from apiVersion
        group = spec.api_version.split("/")[0] if "/" in spec.api_version else ""
        version = spec.api_version.split("/")[-1]
        for (av, kind), info in self._by_kind.items():
            if kind != spec.kind:
                continue
            if group and group in av and version in av:
                return info
            if not group and av == version:
                return info
        # Last resort: kind only unique
        matches = [info for (av, kind), info in self._by_kind.items() if kind == spec.kind]
        if len(matches) == 1:
            return matches[0]
        return None


def list_objects(spec: WatchSpec, resource: dict[str, Any]) -> list[dict[str, Any]]:
    name = resource["name"]
    args = ["get", name, "-o", "json"]
    if resource.get("namespaced", True) and not spec.cluster_scoped:
        args.insert(2, "-A")
    ls = spec.label_selector()
    fs = spec.field_selector()
    if ls:
        args.extend(["-l", ls])
    if fs:
        args.extend(["--field-selector", fs])
    rc, out, err = run_oc(args, timeout=600)
    if rc != 0:
        # Retry without -A for cluster-scoped
        if "-A" in args:
            args = [a for a in args if a != "-A"]
            rc, out, err = run_oc(args, timeout=600)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip() or f"oc get {name} failed")
    data = json.loads(out)
    if data.get("kind", "").endswith("List"):
        return list(data.get("items") or [])
    return [data]


def probe_console_rss() -> dict[str, str]:
    """Best-effort RSS / metrics for the console backend pod via bash helpers."""
    info: dict[str, str] = {}
    script = f"""
set -euo pipefail
source "{SCRIPT_DIR}/lib.sh"
load_env
require_oc
resolve_console_deploy
resolve_console_pod || true
echo "DEPLOY=${{CONSOLE_DEPLOY:-}}"
echo "NS=${{CONSOLE_NS:-}}"
echo "POD=${{CONSOLE_POD:-}}"
if [[ -n "${{CONSOLE_POD:-}}" ]]; then
  oc exec -n "${{CONSOLE_NS}}" "${{CONSOLE_POD}}" -- sh -c \
    'grep -E "^(VmRSS|VmSize|VmPeak):" /proc/1/status' 2>/dev/null || true
  oc adm top pod -n "${{CONSOLE_NS}}" "${{CONSOLE_POD}}" --containers 2>/dev/null || true
fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    info["raw"] = (result.stdout or "") + (result.stderr or "")
    for line in (result.stdout or "").splitlines():
        if line.startswith("DEPLOY="):
            info["deploy"] = line.split("=", 1)[1]
        elif line.startswith("NS="):
            info["ns"] = line.split("=", 1)[1]
        elif line.startswith("POD="):
            info["pod"] = line.split("=", 1)[1]
        elif line.startswith("VmRSS:"):
            info["vmrss"] = line.split(":", 1)[1].strip()
        elif line.startswith("VmSize:"):
            info["vmsize"] = line.split(":", 1)[1].strip()
        elif line.startswith("VmPeak:"):
            info["vmpeak"] = line.split(":", 1)[1].strip()
    return info


def search_kind_counts(kinds: set[str]) -> dict[str, int | None]:
    """Query search-api GraphQL for item counts by kind (best effort)."""
    out: dict[str, int | None] = {k: None for k in kinds}
    # Discover search route / service
    rc, route, _ = run_oc(
        [
            "get",
            "route",
            "-A",
            "-o",
            "jsonpath={range .items[?(@.metadata.name==\"search-api\")]}{.spec.host}{\"\\n\"}{end}",
        ]
    )
    lines = (route or "").strip().splitlines() if rc == 0 else []
    host = lines[0] if lines else ""
    if not host:
        return out

    token_rc, token, _ = run_oc(["whoami", "-t"])
    if token_rc != 0 or not token.strip():
        return out

    # Single batched query is complex; do one kind at a time with limit 0 if supported,
    # else count items with high limit truncated.
    for kind in sorted(kinds):
        query = {
            "query": "query($input:[SearchInput]){searchResult:search(input:$input){count}}",
            "variables": {
                "input": [{"keywords": [], "filters": [{"property": "kind", "values": [kind]}], "limit": 1}]
            },
        }
        # Prefer in-cluster port-forward style is heavy; try route HTTPS.
        try:
            import urllib.request
            import ssl

            req = urllib.request.Request(
                f"https://{host}/searchapi/graphql",
                data=json.dumps(query).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token.strip()}",
                },
                method="POST",
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                body = json.loads(resp.read().decode())
            results = body.get("data", {}).get("searchResult") or []
            if results and isinstance(results[0], dict) and "count" in results[0]:
                out[kind] = int(results[0]["count"])
        except Exception:
            out[kind] = None
    return out


def measure_spec(spec: WatchSpec, api: ApiResourceCache) -> SpecReport:
    report = SpecReport(spec=spec)
    resource = api.resolve(spec)
    if resource is None:
        report.status = "not-installed"
        report.error = f"no api-resource for {spec.api_version}/{spec.kind}"
        return report
    try:
        items = list_objects(spec, resource)
    except RuntimeError as exc:
        msg = str(exc)
        if "NotFound" in msg or "the server doesn't have a resource type" in msg or "no matches" in msg.lower():
            report.status = "not-installed"
        else:
            report.status = "error"
        report.error = msg[:300]
        return report

    sizes: list[int] = []
    for obj in items:
        # Ensure apiVersion/kind present like transformFor
        if "apiVersion" not in obj:
            obj = {**obj, "apiVersion": spec.api_version}
        if "kind" not in obj:
            obj = {**obj, "kind": spec.kind}
        sizes.append(object_bytes(obj, spec.keep_managed_fields))
        if len(report.sample_names) < 3:
            m = obj.get("metadata") or {}
            n = m.get("name", "?")
            ns = m.get("namespace")
            report.sample_names.append(f"{ns}/{n}" if ns else str(n))

    sizes_sorted = sorted(sizes)
    report.count = len(sizes)
    report.bytes_total = sum(sizes)
    report.bytes_max = sizes_sorted[-1] if sizes_sorted else 0
    report.bytes_p50 = percentile(sizes_sorted, 0.5)
    return report


def write_csv(path: Path, rows: list[SpecReport], total_bytes: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "apiVersion",
                "kind",
                "mode",
                "selector",
                "status",
                "count",
                "bytes",
                "bytes_human",
                "max",
                "p50",
                "pct_total",
                "search_count",
                "migration_hint",
                "error",
            ]
        )
        for r in rows:
            pct = (100.0 * r.bytes_total / total_bytes) if total_bytes else 0.0
            w.writerow(
                [
                    r.spec.api_version,
                    r.spec.kind,
                    r.mode,
                    r.spec.short_selector(),
                    r.status,
                    r.count,
                    r.bytes_total,
                    human_bytes(r.bytes_total),
                    r.bytes_max,
                    r.bytes_p50,
                    f"{pct:.2f}",
                    "" if r.search_count is None else r.search_count,
                    r.hint,
                    r.error,
                ]
            )


def write_markdown(
    path: Path,
    rows: list[SpecReport],
    total_bytes: int,
    sse_bytes: int,
    rss: dict[str, str],
    elapsed: float,
) -> None:
    lines: list[str] = []
    lines.append("# Console informer cache memory report")
    lines.append("")
    lines.append(f"Generated: `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}`")
    lines.append(f"Watch specs: `{len(rows)}` (must match `DefaultWatchSpecs`)")
    lines.append(f"Estimated JSON cache total: **{human_bytes(total_bytes)}** (`{total_bytes}` bytes)")
    lines.append(f"SSE-forwarded subset total: **{human_bytes(sse_bytes)}**")
    lines.append(f"Elapsed: `{elapsed:.1f}s`")
    lines.append("")
    lines.append("## Console pod RSS (process)")
    lines.append("")
    lines.append(f"- Deploy: `{rss.get('deploy', 'n/a')}` ns `{rss.get('ns', 'n/a')}`")
    lines.append(f"- Pod: `{rss.get('pod', 'n/a')}`")
    lines.append(f"- VmRSS: `{rss.get('vmrss', 'n/a')}`")
    lines.append(f"- VmSize: `{rss.get('vmsize', 'n/a')}`")
    lines.append("")
    lines.append(
        "Go heap is typically several times the JSON estimate (map overhead). "
        "Use the **ranking** to choose migration targets, not absolute equality with RSS."
    )
    lines.append("")
    lines.append("## Top consumers (by estimated bytes)")
    lines.append("")
    lines.append("| Rank | Mode | Kind | Selector | Count | Bytes | % | Hint | Search |")
    lines.append("|-----:|------|------|----------|------:|------:|--:|------|-------:|")
    ranked = sorted(rows, key=lambda r: r.bytes_total, reverse=True)
    for i, r in enumerate(ranked, 1):
        if r.status != "ok" and r.bytes_total == 0:
            continue
        pct = (100.0 * r.bytes_total / total_bytes) if total_bytes else 0.0
        sc = "" if r.search_count is None else str(r.search_count)
        lines.append(
            f"| {i} | `{r.mode}` | `{r.spec.kind}` (`{r.spec.api_version}`) | `{r.spec.short_selector()}` "
            f"| {r.count} | {human_bytes(r.bytes_total)} | {pct:.1f}% | `{r.hint}` | {sc} |"
        )
    lines.append("")
    lines.append("## Migration guide")
    lines.append("")
    lines.append("| Hint | Meaning |")
    lines.append("|------|---------|")
    lines.append("| `keep-aggregate` | Required by `POST /aggregate` — keep in informer |")
    lines.append("| `keep-global` | Used by most pages via LoadData — migrate last |")
    lines.append("| `keep-selector` | Narrow Secret/ConfigMap/CSR/Service watches |")
    lines.append("| `keep-cache-only` | Single/small objects, not on SSE |")
    lines.append("| `candidate-fleet-search` | Good first mover to `useFleetSearch` |")
    lines.append("| `polled-aggregate` | Argo polled apps — aggregate, not SSE |")
    lines.append("| `review` | Inspect UI field needs before moving |")
    lines.append("")
    candidates = [r for r in ranked if r.hint == "candidate-fleet-search" and r.bytes_total > 0]
    lines.append("### Suggested first movers")
    lines.append("")
    if not candidates:
        lines.append("_No candidate rows with data yet — apply `./apply-layer-console.sh --preset target`._")
    else:
        for r in candidates[:10]:
            lines.append(
                f"1. **{r.spec.kind}** — {human_bytes(r.bytes_total)} "
                f"({r.count} objects, search={r.search_count})"
            )
    lines.append("")
    lines.append("## Not installed / errors")
    lines.append("")
    missing = [r for r in rows if r.status != "ok"]
    if not missing:
        lines.append("_None_")
    else:
        for r in missing:
            lines.append(f"- `{r.spec.api_version}/{r.spec.kind}` — **{r.status}**: {r.error}")
    lines.append("")
    lines.append("## Cross-check")
    lines.append("")
    lines.append(
        "Sum of `sse` rows should be close to decompressed `./measure-sse.sh` size "
        "(same hub, same moment). Local backend log `informer cache memory` "
        "(`heapAlloc`, `items`) calibrates the Go heap multiplier."
    )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--skip-rss", action="store_true")
    parser.add_argument("--skip-search", action="store_true")
    parser.add_argument(
        "--only-kind",
        action="append",
        default=[],
        help="Limit to kind name(s); may be repeated",
    )
    args = parser.parse_args()

    try:
        validate_specs()
    except AssertionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rc, who, err = run_oc(["whoami"])
    if rc != 0:
        print(f"error: not logged in ({err})", file=sys.stderr)
        return 1
    print(f"Cluster user: {who.strip()}", file=sys.stderr)

    specs = list(WATCH_SPECS)
    if args.only_kind:
        wanted = set(args.only_kind)
        specs = [s for s in specs if s.kind in wanted]
        if not specs:
            print("error: --only-kind matched nothing", file=sys.stderr)
            return 1

    api = ApiResourceCache()
    print("Loading api-resources ...", file=sys.stderr)
    try:
        api.load()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    t0 = time.time()
    rows: list[SpecReport] = []
    for i, spec in enumerate(specs, 1):
        print(
            f"[{i}/{len(specs)}] {spec.mode} {spec.api_version}/{spec.kind} {spec.short_selector()}",
            file=sys.stderr,
        )
        rows.append(measure_spec(spec, api))

    if not args.skip_search:
        print("Querying search counts (best effort) ...", file=sys.stderr)
        kinds = {r.spec.kind for r in rows if r.bytes_total > 0 or r.hint == "candidate-fleet-search"}
        counts = search_kind_counts(kinds)
        for r in rows:
            if r.spec.kind in counts:
                r.search_count = counts[r.spec.kind]

    rss: dict[str, str] = {}
    if not args.skip_rss:
        print("Probing console pod RSS ...", file=sys.stderr)
        rss = probe_console_rss()

    total_bytes = sum(r.bytes_total for r in rows)
    sse_bytes = sum(r.bytes_total for r in rows if r.mode == "sse")
    elapsed = time.time() - t0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out_dir / "informer-memory.md"
    csv_path = args.out_dir / "informer-memory.csv"
    write_csv(csv_path, rows, total_bytes)
    write_markdown(md_path, rows, total_bytes, sse_bytes, rss, elapsed)

    print(file=sys.stderr)
    print(f"Total estimated JSON: {human_bytes(total_bytes)}", file=sys.stderr)
    print(f"SSE subset:           {human_bytes(sse_bytes)}", file=sys.stderr)
    print(f"Wrote {md_path}", file=sys.stderr)
    print(f"Wrote {csv_path}", file=sys.stderr)

    # Print top 15 to stdout for quick reading
    print("\n# Top 15 by estimated informer bytes\n")
    ranked = sorted(rows, key=lambda r: r.bytes_total, reverse=True)
    print(f"{'bytes':>12}  {'count':>7}  {'mode':10}  {'hint':24}  kind")
    for r in ranked[:15]:
        print(
            f"{human_bytes(r.bytes_total):>12}  {r.count:7d}  {r.mode:10}  {r.hint:24}  "
            f"{r.spec.kind} ({r.spec.api_version}) {r.spec.short_selector()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
