#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Measure ACM console /multicloud/events SSE payload until LOADED (large-fleet HAR metric)."""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import zlib
from typing import BinaryIO
from urllib.parse import urlparse

LOADED_MARKERS = (b'"type":"LOADED"', b'"type": "LOADED"')
TARGET_DECOMPRESSED = int(os.environ.get("SSE_REFERENCE_BYTES", "300006221"))  # optional reference (~300 MiB)
DEFAULT_PROGRESS_INTERVAL = float(os.environ.get("SSE_PROGRESS_INTERVAL", "5"))


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def human_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GiB"
    if n >= 1024**2:
        return f"{n / 1024**2:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def oc_token() -> str:
    result = subprocess.run(["oc", "whoami", "-t"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("oc whoami -t failed — run oc login")
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("empty token from oc whoami -t")
    return token


def oc_console_url() -> str:
    for args in (
        ["get", "route", "-n", "openshift-console", "console", "-o", "jsonpath=https://{.spec.host}"],
        ["get", "routes", "-A", "-o", "jsonpath={range .items[?(@.metadata.name=='console')]}{.spec.host}{\\n}{end}"],
    ):
        result = subprocess.run(["oc", *args], capture_output=True, text=True, check=False)
        host = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if host and not host.startswith("http"):
            return f"https://{host}"
        if host.startswith("http"):
            return host
    raise RuntimeError("could not resolve OpenShift console URL — set CONSOLE_URL")


def detect_plugin() -> str:
    env = os.environ.get("CONSOLE_PLUGIN", "auto")
    if env != "auto":
        return env
    result = subprocess.run(
        ["oc", "get", "mce", "multiclusterengine", "-n", "multicluster-engine"],
        capture_output=True,
        check=False,
    )
    return "mce" if result.returncode == 0 else "acm"


def build_events_url(base: str, plugin: str) -> str:
    base = base.rstrip("/")
    return f"{base}/api/proxy/plugin/{plugin}/console/multicloud/events"


def ssl_context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def stream_sse_wire_and_body(
    url: str,
    token: str,
    *,
    max_seconds: float,
    insecure: bool,
    save_path: str | None,
    alt_paths: list[str] | None = None,
    progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
    progress_enabled: bool = True,
    use_gzip: bool = False,
) -> dict[str, object]:
    """Stream SSE with accurate wire + decompressed byte counts until LOADED."""
    paths = [urlparse(url).path or "/"]
    if alt_paths:
        for p in alt_paths:
            if p not in paths:
                paths.append(p)

    last_error: Exception | None = None
    parsed_base = urlparse(url)
    for path in paths:
        trial_url = parsed_base._replace(path=path).geturl()
        try:
            return _stream_sse_once(
                trial_url,
                token,
                max_seconds=max_seconds,
                insecure=insecure,
                save_path=save_path,
                progress_interval=progress_interval,
                progress_enabled=progress_enabled,
                use_gzip=use_gzip,
            )
        except RuntimeError as exc:
            last_error = exc
            if "HTTP 404" not in str(exc):
                raise
    if last_error:
        raise last_error
    raise RuntimeError("no events path succeeded")


def _log_progress(
    *,
    elapsed: float,
    max_seconds: float,
    wire_bytes: int,
    decompressed_bytes: int,
    event_count: int,
    loaded: bool,
    encoding: str,
) -> None:
    loaded_label = "yes" if loaded else "waiting"
    pct = min(100.0, elapsed / max_seconds * 100) if max_seconds > 0 else 0.0
    print(
        f"[{elapsed:6.1f}s / {max_seconds:.0f}s, {pct:4.0f}%] "
        f"wire={human_bytes(wire_bytes):>10} "
        f"decompressed={human_bytes(decompressed_bytes):>10} "
        f"events~={event_count:5d} "
        f"encoding={encoding} "
        f"LOADED={loaded_label}",
        file=sys.stderr,
        flush=True,
    )


def _stream_sse_once(
    url: str,
    token: str,
    *,
    max_seconds: float,
    insecure: bool,
    save_path: str | None,
    progress_interval: float,
    progress_enabled: bool,
    use_gzip: bool,
) -> dict[str, object]:
    """Single GET attempt against one path."""
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("only https URLs supported")

    host = parsed.hostname
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    ctx = ssl_context(insecure)
    conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=max_seconds + 30)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if use_gzip:
        headers["Accept-Encoding"] = "gzip"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()

    status = resp.status
    content_type = resp.getheader("Content-Type", "")
    encoding = resp.getheader("Content-Encoding", "identity") or "identity"

    if status != 200:
        err_body = resp.read(4096)
        conn.close()
        preview = err_body[:500].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {status} from events endpoint (Content-Type: {content_type}). "
            f"Body preview: {preview!r}. "
            "For hub testing use: SSE_MODE=direct ./measure-sse.sh (port-forward to console backend)."
        )

    if "text/event-stream" not in content_type and "text/plain" not in content_type:
        err_body = resp.read(4096)
        conn.close()
        preview = err_body[:500].decode("utf-8", errors="replace")
        raise RuntimeError(
            f"unexpected Content-Type {content_type!r} (HTTP {status}). Preview: {preview!r}"
        )

    show_progress = progress_enabled and progress_interval > 0
    if show_progress:
        encoding_note = encoding if use_gzip else "identity (default; same decompressed metric as browser gzip)"
        print(
            f"streaming SSE (HTTP {status}, {encoding_note}) — progress every {progress_interval:g}s",
            file=sys.stderr,
            flush=True,
        )

    wire_bytes = 0
    decompressed_bytes = 0
    event_count = 0
    loaded = False
    buffer = b""
    start = time.monotonic()
    last_progress = start
    stall_ticks = 0
    last_wire_progress = 0
    read_size = 8192 if use_gzip else 65536
    save_fp: BinaryIO | None = open(save_path, "wb") if save_path else None
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None

    def maybe_log_progress(*, force: bool = False) -> None:
        nonlocal last_progress, stall_ticks, last_wire_progress
        if not show_progress:
            return
        now = time.monotonic()
        if not force and now - last_progress < progress_interval:
            return
        if wire_bytes > 0 and wire_bytes == last_wire_progress:
            stall_ticks += 1
        else:
            stall_ticks = 0
        last_wire_progress = wire_bytes
        last_progress = now
        _log_progress(
            elapsed=now - start,
            max_seconds=max_seconds,
            wire_bytes=wire_bytes,
            decompressed_bytes=decompressed_bytes,
            event_count=event_count,
            loaded=loaded,
            encoding=encoding,
        )
        if stall_ticks >= 3 and encoding == "gzip":
            print(
                "warning: wire bytes stalled with gzip (Node/http.client backpressure) — "
                "retry without SSE_GZIP / --gzip (identity is valid for decompressed size)",
                file=sys.stderr,
                flush=True,
            )
            stall_ticks = 0

    stop_progress = threading.Event()
    progress_thread: threading.Thread | None = None
    if show_progress:

        def progress_worker() -> None:
            while not stop_progress.wait(progress_interval):
                maybe_log_progress(force=True)

        progress_thread = threading.Thread(target=progress_worker, daemon=True)
        progress_thread.start()
        maybe_log_progress(force=True)

    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= max_seconds:
                break

            remaining = max_seconds - elapsed
            if conn.sock is not None:
                conn.sock.settimeout(remaining)

            try:
                if hasattr(resp, "read1"):
                    wire_chunk = resp.read1(read_size)
                else:
                    wire_chunk = resp.read(read_size)
            except socket.timeout:
                break

            if not wire_chunk:
                break
            wire_bytes += len(wire_chunk)

            if decompressor is not None:
                chunk = decompressor.decompress(wire_chunk)
            else:
                chunk = wire_chunk

            if not chunk and not wire_chunk:
                break

            decompressed_bytes += len(chunk)
            buffer += chunk
            if save_fp and chunk:
                save_fp.write(chunk)

            event_count += chunk.count(b"\n\n")
            if any(marker in buffer for marker in LOADED_MARKERS):
                loaded = True
                maybe_log_progress(force=True)
                break

            if len(buffer) > 4_000_000:
                buffer = buffer[-2_000_000:]

            maybe_log_progress()
    finally:
        stop_progress.set()
        if progress_thread is not None:
            progress_thread.join(timeout=progress_interval + 2)
        conn.close()
        if save_fp:
            save_fp.close()

    elapsed = time.monotonic() - start
    if show_progress and not loaded:
        _log_progress(
            elapsed=elapsed,
            max_seconds=max_seconds,
            wire_bytes=wire_bytes,
            decompressed_bytes=decompressed_bytes,
            event_count=event_count,
            loaded=loaded,
            encoding=encoding,
        )
        print(
            f"stopped after {elapsed:.1f}s without LOADED (limit {max_seconds:.0f}s)",
            file=sys.stderr,
            flush=True,
        )
    return {
        "url": url,
        "http_status": status,
        "content_type": content_type,
        "encoding": encoding,
        "wire_bytes": wire_bytes,
        "decompressed_bytes": decompressed_bytes,
        "elapsed_seconds": round(elapsed, 2),
        "event_blocks_estimate": event_count,
        "loaded": loaded,
        "save_path": save_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure console SSE /events until LOADED")
    parser.add_argument("--url", help="Full events URL (default: detect from oc + plugin)")
    parser.add_argument("--console-url", default=os.environ.get("CONSOLE_URL", ""))
    parser.add_argument("--plugin", default=os.environ.get("CONSOLE_PLUGIN", "auto"))
    parser.add_argument("--token", default=os.environ.get("OC_TOKEN", ""))
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--insecure", action="store_true", default=True)
    parser.add_argument("--save", help="Write decompressed stream bytes to file")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="Seconds between progress lines on stderr (0 disables; env SSE_PROGRESS_INTERVAL)",
    )
    parser.add_argument("--quiet", action="store_true", help="Disable progress lines on stderr")
    parser.add_argument(
        "--gzip",
        action="store_true",
        default=env_flag("SSE_GZIP"),
        help="Request gzip Content-Encoding (may stall with http.client; env SSE_GZIP)",
    )
    args = parser.parse_args()

    progress_enabled = not args.quiet and not args.json and args.progress_interval > 0

    try:
        token = args.token or oc_token()
        base = args.console_url or oc_console_url()
        plugin = args.plugin if args.plugin != "auto" else detect_plugin()
        url = args.url or build_events_url(base, plugin)
        stats = stream_sse_wire_and_body(
            url,
            token,
            max_seconds=args.max_seconds,
            insecure=args.insecure,
            save_path=args.save,
            alt_paths=["/multicloud/events", "/events"],
            progress_interval=args.progress_interval,
            progress_enabled=progress_enabled,
            use_gzip=args.gzip,
        )
    except (RuntimeError, urllib.error.URLError, OSError, zlib.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    decomp = int(stats["decompressed_bytes"])
    wire = stats.get("wire_bytes")
    pct = (decomp / TARGET_DECOMPRESSED * 100) if TARGET_DECOMPRESSED else 0

    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    print("=== ACM console SSE measurement ===")
    print(f"URL:                 {stats['url']}")
    if stats.get("http_status"):
        print(f"HTTP status:         {stats['http_status']}")
    if stats.get("content_type"):
        print(f"Content-Type:        {stats['content_type']}")
    print(f"Content-Encoding:    {stats['encoding']}")
    print(f"Elapsed:             {stats['elapsed_seconds']}s")
    print(f"LOADED seen:         {stats['loaded']}")
    print(f"Decompressed size:   {decomp} bytes ({human_bytes(decomp)})")
    if wire is not None:
        wire_label = "Wire size (gzip)" if stats.get("encoding") == "gzip" else "Wire size"
        print(f"{wire_label + ':':<22}{wire} bytes ({human_bytes(int(wire))})")
    print(f"Event blocks (~):    {stats['event_blocks_estimate']}")
    print(f"Reference target:   {TARGET_DECOMPRESSED} bytes ({human_bytes(TARGET_DECOMPRESSED)})")
    print(f"vs reference HAR:     {pct:.1f}% of reference decompressed payload")
    if args.save:
        print(f"Saved decompressed:  {args.save}")
    if not stats["loaded"]:
        print("warning: LOADED not seen — increase --max-seconds or check auth/proxy", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
