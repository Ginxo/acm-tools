#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Load and expand contract catalog YAML for performance benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ExpandedCase:
    id: str
    group: str
    kind: str
    description: str
    method: str
    path: str
    auth: str
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    raw_body: str = ""
    content_type: str = ""
    soft: bool = False
    soft_statuses: list[int] = field(default_factory=list)
    timeout_seconds: int = 0
    expect_status: list[int] = field(default_factory=lambda: [200])
    ws: dict[str, Any] | None = None
    multicloud_variant: bool = False

    def report_id(self) -> str:
        if self.multicloud_variant and not self.id.endswith("/multicloud"):
            return f"{self.id}/multicloud"
        return self.id


def multicloud_path(path: str) -> str:
    if path.startswith("/multicloud/") or path == "/multicloud":
        return path
    if not path.startswith("/"):
        path = "/" + path
    return "/multicloud" + path


def normalize_case(raw: dict[str, Any]) -> dict[str, Any]:
    case = dict(raw)
    case.setdefault("kind", "rest")
    case.setdefault("method", "GET")
    case.setdefault("auth", "none")
    case.setdefault("headers", {})
    expect = case.get("expect") or {}
    if not expect.get("status"):
        expect["status"] = [200]
    case["expect"] = expect
    return case


def load_catalog(catalog_dir: Path, group: str | None = None) -> list[ExpandedCase]:
    cases: list[ExpandedCase] = []
    for path in sorted(catalog_dir.glob("*.yaml")):
        if path.name in ("80-negative.yaml", "watched-resources.yaml"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("cases") or []:
            item = normalize_case(raw)
            auth = str(item.get("auth", "none")).lower()
            if auth == "invalid":
                continue
            if group and item.get("group") != group:
                continue
            expect = item.get("expect") or {}
            base = ExpandedCase(
                id=str(item["id"]),
                group=str(item.get("group", "")),
                kind=str(item.get("kind", "rest")),
                description=str(item.get("description", "")),
                method=str(item.get("method", "GET")),
                path=str(item["path"]),
                auth=auth,
                headers=dict(item.get("headers") or {}),
                body=item.get("body"),
                raw_body=str(item.get("rawBody") or ""),
                content_type=str(item.get("contentType") or ""),
                soft=bool(item.get("soft")),
                soft_statuses=[int(x) for x in (item.get("softStatuses") or [])],
                timeout_seconds=int(item.get("timeoutSeconds") or 0),
                expect_status=[int(x) for x in expect.get("status") or [200]],
                ws=item.get("ws"),
                multicloud_variant=False,
            )
            cases.append(base)
            if item.get("alsoMulticloud"):
                mc = ExpandedCase(**{**asdict(base), "path": multicloud_path(base.path), "multicloud_variant": True})
                cases.append(mc)
    if not cases:
        raise RuntimeError(f"no cases loaded from {catalog_dir}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Load contract catalog cases")
    parser.add_argument(
        "--catalog-dir",
        default=str(Path(__file__).resolve().parent / "catalog"),
        help="Directory containing catalog YAML files",
    )
    parser.add_argument("--group", default="", help="Filter by CONTRACT_GROUP")
    parser.add_argument("--json", action="store_true", help="Print expanded cases as JSON")
    args = parser.parse_args()

    cases = load_catalog(Path(args.catalog_dir), group=args.group or None)
    if args.json:
        payload = [
            {
                **asdict(c),
                "report_id": c.report_id(),
            }
            for c in cases
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(f"cases={len(cases)} catalog={args.catalog_dir}")
        for i, c in enumerate(cases, 1):
            print(f"{i:3d}. {c.report_id():40s} {c.method:6s} {c.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
