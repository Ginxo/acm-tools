#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Sample CPU and memory of the backend process listening on a TCP port."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def find_pid_on_port(port: int) -> int:
    for cmd in (
        ["ss", "-lptn", f"sport = :{port}"],
        ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if result.returncode != 0:
            continue
        if cmd[0] == "ss":
            for line in result.stdout.splitlines():
                if "pid=" in line:
                    fragment = line.split("pid=", 1)[1]
                    pid_str = fragment.split(",", 1)[0]
                    if pid_str.isdigit():
                        return int(pid_str)
        else:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line)
    raise RuntimeError(f"no listening process found on port {port}")


def read_rss_bytes(pid: int) -> int:
    status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return 0


def read_cpu_jiffies(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()
    if len(stat) < 15:
        return 0
    return int(stat[13]) + int(stat[14])


def read_total_cpu_jiffies() -> int:
    for line in Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("cpu "):
            parts = line.split()[1:]
            return sum(int(x) for x in parts[:10])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample backend process CPU and RSS")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", required=True, help="Write summary JSON on exit")
    args = parser.parse_args()

    pid = args.pid or find_pid_on_port(args.port)
    ncpu = max(1, os.cpu_count() or 1)
    samples: list[dict[str, float | int]] = []
    stop = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    prev_proc = read_cpu_jiffies(pid)
    prev_total = read_total_cpu_jiffies()
    prev_ts = time.monotonic()
    start_rss = read_rss_bytes(pid)

    while not stop:
        time.sleep(args.interval)
        now = time.monotonic()
        rss = read_rss_bytes(pid)
        proc = read_cpu_jiffies(pid)
        total = read_total_cpu_jiffies()
        dt = now - prev_ts
        dproc = proc - prev_proc
        dtotal = total - prev_total
        cpu_pct = 0.0
        if dt > 0 and dtotal > 0:
            cpu_pct = 100.0 * (dproc / dtotal) * ncpu
        samples.append({"rss_bytes": rss, "cpu_percent": round(cpu_pct, 2), "ts": now})
        prev_proc, prev_total, prev_ts = proc, total, now

    rss_values = [int(s["rss_bytes"]) for s in samples]
    cpu_values = [float(s["cpu_percent"]) for s in samples]
    end_rss = rss_values[-1] if rss_values else start_rss
    summary = {
        "pid": pid,
        "port": args.port,
        "samples": len(samples),
        "cpu_percent": {
            "avg": round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else 0.0,
            "peak": round(max(cpu_values), 2) if cpu_values else 0.0,
        },
        "memory_rss_bytes": {
            "start": start_rss,
            "peak": max(rss_values) if rss_values else start_rss,
            "end": end_rss,
            "avg": round(sum(rss_values) / len(rss_values)) if rss_values else start_rss,
        },
    }
    Path(args.output).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
