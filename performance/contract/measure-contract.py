#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Run contract catalog cases against a live backend and measure performance."""

from __future__ import annotations

import argparse
import base64
import gzip
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
EVENTS_DIR = SCRIPT_DIR.parent / "events"
sys.path.insert(0, str(SCRIPT_DIR))

from load_catalog import ExpandedCase, load_catalog  # noqa: E402


def _load_measure_sse() -> tuple[Any, Any]:
    import importlib.util

    path = EVENTS_DIR / "measure-sse.py"
    if not path.is_file():
        return None, None
    spec = importlib.util.spec_from_file_location("measure_sse", path)
    if spec is None or spec.loader is None:
        return None, None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.stream_sse_wire_and_body, module.human_bytes


stream_sse_wire_and_body, human_bytes = _load_measure_sse()


def env_flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name, "")
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def human_size(n: int) -> str:
    if human_bytes:
        return human_bytes(n)
    if n >= 1024**2:
        return f"{n / 1024**2:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def oc_token() -> str:
    token = os.environ.get("CONTRACT_TOKEN", "").strip()
    if token:
        return token
    result = subprocess.run(["oc", "whoami", "-t"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("CONTRACT_TOKEN empty and oc whoami -t failed")
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("empty token from oc whoami -t")
    return token


def resolve_url(base: str, path: str, path_prefix: str = "") -> str:
    p = path if path.startswith("/") else "/" + path
    if path_prefix and not p.startswith(path_prefix + "/") and p != path_prefix:
        p = path_prefix.rstrip("/") + p
    return base.rstrip("/") + p


def apply_auth_headers(headers: dict[str, str], auth: str, token: str) -> dict[str, str]:
    out = dict(headers)
    auth = auth.lower()
    if not token:
        return out
    if auth in ("bearer", "both"):
        out["Authorization"] = f"Bearer {token}"
    if auth in ("cookie", "both"):
        out["Cookie"] = f"acm-access-token-cookie={token}"
    return out


def ssl_context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def progress_prefix(run_index: int, run_total: int, case_index: int, case_total: int) -> str:
    pct = round(100.0 * case_index / case_total, 1) if case_total else 0.0
    return f"[run {run_index}/{run_total}] [{case_index}/{case_total}] ({pct}%)"


def log_progress(msg: str) -> None:
    if env_flag("PERF_VERBOSE", True):
        print(msg, file=sys.stderr, flush=True)


def round_elapsed(seconds: float) -> float:
    """Keep millisecond precision so sub-5ms requests are not reported as 0.00s."""
    return round(seconds, 3)


def format_elapsed(seconds: float) -> str:
    elapsed = float(seconds)
    if elapsed < 0.001:
        return f"{elapsed * 1000:.2f}ms"
    if elapsed < 1:
        return f"{elapsed * 1000:.1f}ms"
    return f"{elapsed:.3f}s"


def evaluate_result(
    http_status: int,
    expect_status: list[int],
    soft: bool,
    soft_statuses: list[int],
    *,
    timed_out: bool = False,
    error: str = "",
    loaded: bool | None = None,
) -> tuple[bool, bool]:
    """Return (success, skipped)."""
    if timed_out or error:
        return False, soft
    if loaded is not None and not loaded:
        return False, soft
    if http_status in expect_status:
        return True, False
    if soft and (not soft_statuses or http_status in soft_statuses):
        return False, True
    return False, soft


def decode_body(encoding: str, body: bytes) -> bytes:
    enc = (encoding or "identity").lower().strip()
    if enc in ("", "identity"):
        return body
    if enc == "gzip":
        return gzip.decompress(body)
    if enc == "deflate":
        return zlib.decompress(body)
    return body


def measure_rest(
    case: ExpandedCase,
    backend_url: str,
    token: str,
    *,
    path_prefix: str,
    insecure: bool,
    http_timeout: int,
) -> dict[str, Any]:
    url = resolve_url(backend_url, case.path, path_prefix)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = apply_auth_headers(case.headers, case.auth, token)
    body_bytes = b""
    if case.raw_body:
        body_bytes = case.raw_body.encode("utf-8")
    elif case.body is not None:
        body_bytes = json.dumps(case.body).encode("utf-8")
    if body_bytes and "Content-Type" not in headers:
        headers["Content-Type"] = case.content_type or "application/json"
    elif case.content_type:
        headers["Content-Type"] = case.content_type

    timeout = case.timeout_seconds or http_timeout
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    start = time.monotonic()
    timed_out = False
    http_status = 0
    response_bytes = 0
    error = ""

    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, context=ssl_context(insecure), timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request(case.method, path, body=body_bytes or None, headers=headers)
        resp = conn.getresponse()
        http_status = resp.status
        raw = resp.read(32 * 1024 * 1024)
        decoded = decode_body(resp.getheader("Content-Encoding", ""), raw)
        response_bytes = len(decoded)
        conn.close()
    except socket.timeout:
        timed_out = True
        error = f"TIMEOUT ({timeout}s)"
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    elapsed = round_elapsed(time.monotonic() - start)

    success, skipped = evaluate_result(
        http_status,
        case.expect_status,
        case.soft,
        case.soft_statuses,
        timed_out=timed_out,
        error=error,
    )
    return {
        "id": case.report_id(),
        "group": case.group,
        "path": case.path,
        "kind": case.kind,
        "method": case.method,
        "elapsed_seconds": elapsed,
        "elapsed_ms": round(elapsed * 1000, 1),
        "http_status": http_status,
        "response_bytes": response_bytes,
        "success": success,
        "skipped": skipped,
        "soft": case.soft,
        "timed_out": timed_out,
        "error": error or None,
    }


def measure_sse(
    case: ExpandedCase,
    backend_url: str,
    token: str,
    *,
    path_prefix: str,
    insecure: bool,
    progress_cb: Any,
) -> dict[str, Any]:
    if stream_sse_wire_and_body is None:
        return {
            "id": case.report_id(),
            "path": case.path,
            "kind": "sse",
            "elapsed_seconds": 0.0,
            "http_status": 0,
            "response_bytes": 0,
            "success": False,
            "skipped": True,
            "error": "measure-sse.py not importable",
        }
    url = resolve_url(backend_url, case.path, path_prefix)
    max_seconds = float(case.timeout_seconds or env_int("CONTRACT_SSE_TIMEOUT", 120))
    progress_interval = env_float("PERF_PROGRESS_INTERVAL", 5.0)
    start = time.monotonic()

    def heartbeat() -> None:
        if progress_cb:
            progress_cb(elapsed=time.monotonic() - start, loaded=False, response_bytes=0)

    try:
        result = stream_sse_wire_and_body(
            url,
            token,
            max_seconds=max_seconds,
            insecure=insecure,
            save_path=None,
            progress_interval=progress_interval,
            progress_enabled=env_flag("PERF_VERBOSE", True),
            use_gzip="gzip" in {k.lower(): v for k, v in case.headers.items()}.get("accept-encoding", "").lower(),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = round_elapsed(time.monotonic() - start)
        skipped = case.soft
        return {
            "id": case.report_id(),
            "group": case.group,
            "path": case.path,
            "kind": "sse",
            "method": case.method,
            "elapsed_seconds": elapsed,
            "elapsed_ms": round(elapsed * 1000, 1),
            "http_status": 0,
            "response_bytes": 0,
            "decompressed_bytes": 0,
            "event_blocks_estimate": 0,
            "loaded": False,
            "success": False,
            "skipped": skipped,
            "soft": case.soft,
            "error": str(exc),
        }

    http_status = int(result.get("http_status", 0))
    loaded = bool(result.get("loaded"))
    success, skipped = evaluate_result(
        http_status,
        case.expect_status,
        case.soft,
        case.soft_statuses,
        loaded=loaded,
    )
    return {
        "id": case.report_id(),
        "group": case.group,
        "path": case.path,
        "kind": "sse",
        "method": case.method,
        "elapsed_seconds": round_elapsed(float(result.get("elapsed_seconds", 0))),
        "elapsed_ms": round(float(result.get("elapsed_seconds", 0)) * 1000, 1),
        "http_status": http_status,
        "response_bytes": int(result.get("decompressed_bytes", 0)),
        "decompressed_bytes": int(result.get("decompressed_bytes", 0)),
        "wire_bytes": int(result.get("wire_bytes", 0)),
        "event_blocks_estimate": int(result.get("event_blocks_estimate", 0)),
        "loaded": loaded,
        "success": success,
        "skipped": skipped,
        "soft": case.soft,
        "error": None,
    }


def ws_recv_text(sock: socket.socket, timeout: float) -> str:
    sock.settimeout(timeout)
    header = sock.recv(2)
    if len(header) < 2:
        raise RuntimeError("websocket closed before message")
    b1, b2 = header[0], header[1]
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    length = b2 & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), "big")
    elif length == 127:
        length = int.from_bytes(sock.recv(8), "big")
    mask = sock.recv(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            break
        payload += chunk
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if opcode == 0x8:
        raise RuntimeError("websocket close frame")
    return payload.decode("utf-8", errors="replace")


def ws_send_text(sock: socket.socket, message: str) -> None:
    data = message.encode("utf-8")
    frame = bytearray([0x81])
    if len(data) < 126:
        frame.append(len(data))
    elif len(data) < 65536:
        frame.append(126)
        frame.extend(len(data).to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(len(data).to_bytes(8, "big"))
    frame.extend(data)
    sock.sendall(frame)


def measure_websocket(
    case: ExpandedCase,
    backend_url: str,
    token: str,
    *,
    path_prefix: str,
    insecure: bool,
) -> dict[str, Any]:
    url = resolve_url(backend_url, case.path, path_prefix)
    parsed = urlparse(url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = parsed._replace(scheme=ws_scheme).geturl()
    parsed = urlparse(ws_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    headers = apply_auth_headers(case.headers, case.auth, token)
    ws_spec = case.ws or {}
    subprotocol = str(ws_spec.get("subprotocol") or "")
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if subprotocol:
        lines.append(f"Sec-WebSocket-Protocol: {subprotocol}")
    for k, v in headers.items():
        lines.append(f"{k}: {v}")
    lines.extend(["", ""])
    request = "\r\n".join(lines).encode("utf-8")

    timeout = float(case.timeout_seconds or 20)
    start = time.monotonic()
    error = ""
    http_status = 0
    success = False

    try:
        raw_sock = socket.create_connection((host, port), timeout=timeout)
        if parsed.scheme == "wss":
            sock = ssl_context(env_flag("CONTRACT_TLS_INSECURE", True)).wrap_socket(raw_sock, server_hostname=host)
        else:
            sock = raw_sock
        sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        http_status = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
        if http_status != 101:
            raise RuntimeError(f"websocket upgrade failed: {status_line}")
        if ws_spec.get("expectUpgrade"):
            success = True
        else:
            for msg in ws_spec.get("send") or []:
                ws_send_text(sock, str(msg))
            expect_type = str(ws_spec.get("expectType") or "")
            if expect_type:
                payload = ws_recv_text(sock, timeout)
                data = json.loads(payload)
                success = data.get("type") == expect_type
            else:
                success = True
        sock.close()
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    elapsed = round_elapsed(time.monotonic() - start)
    if not success and not error:
        error = "websocket expectation not met"
    skipped = case.soft and (bool(error) or not success)
    if case.soft and http_status in case.soft_statuses:
        skipped = True
    return {
        "id": case.report_id(),
        "group": case.group,
        "path": case.path,
        "kind": "websocket",
        "method": case.method,
        "elapsed_seconds": elapsed,
        "elapsed_ms": round(elapsed * 1000, 1),
        "http_status": http_status,
        "response_bytes": 0,
        "success": success and not error,
        "skipped": skipped,
        "soft": case.soft,
        "error": error or None,
    }


def measure_case(
    case: ExpandedCase,
    backend_url: str,
    token: str,
    *,
    path_prefix: str,
    insecure: bool,
    http_timeout: int,
    progress_cb: Any = None,
) -> dict[str, Any]:
    kind = case.kind.lower()
    if kind == "sse":
        return measure_sse(case, backend_url, token, path_prefix=path_prefix, insecure=insecure, progress_cb=progress_cb)
    if kind == "websocket":
        return measure_websocket(case, backend_url, token, path_prefix=path_prefix, insecure=insecure)
    return measure_rest(
        case,
        backend_url,
        token,
        path_prefix=path_prefix,
        insecure=insecure,
        http_timeout=http_timeout,
    )


def format_result_line(prefix: str, case: ExpandedCase, result: dict[str, Any]) -> str:
    if result.get("timed_out"):
        mark = "✗"
        detail = result.get("error") or "TIMEOUT"
    elif result.get("skipped"):
        mark = "~"
        detail = result.get("error") or "soft skip"
    elif result.get("success"):
        mark = "✓"
        extra = " LOADED" if result.get("loaded") else ""
        detail = (
            f"{format_elapsed(result['elapsed_seconds'])}  {result.get('http_status', 0)}  "
            f"{human_size(int(result.get('response_bytes', 0)))}{extra}"
        )
    else:
        mark = "✗"
        detail = result.get("error") or f"status {result.get('http_status', 0)}"
    return f"{prefix} {mark} {case.report_id()}  {detail}"


def run_all_cases(
    cases: list[ExpandedCase],
    *,
    run_index: int,
    run_total: int,
    backend_url: str,
    token: str,
    path_prefix: str,
    insecure: bool,
    http_timeout: int,
) -> list[dict[str, Any]]:
    total = len(cases)
    results: list[dict[str, Any]] = []
    log_progress(
        f"[run {run_index}/{run_total}] warmup=false  cases={total}  backend={backend_url}"
    )
    for idx, case in enumerate(cases, 1):
        prefix = progress_prefix(run_index, run_total, idx, total)
        log_progress(f"{prefix} → {case.method} {case.path}  (id={case.report_id()}, kind={case.kind})")

        def sse_progress(**kwargs: Any) -> None:
            elapsed = kwargs.get("elapsed", 0.0)
            log_progress(
                f"{prefix} … {case.report_id()}  streaming  {elapsed:.1f}s  (waiting LOADED)"
            )

        result = measure_case(
            case,
            backend_url,
            token,
            path_prefix=path_prefix,
            insecure=insecure,
            http_timeout=http_timeout,
            progress_cb=sse_progress,
        )
        results.append(result)
        log_progress(format_result_line(prefix, case, result))
    return results


def probe_backend(backend_url: str, insecure: bool, path_prefix: str = "") -> None:
    url = resolve_url(backend_url, "/ping", path_prefix)
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, context=ssl_context(insecure), timeout=8)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=8)
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f"GET /ping -> {resp.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure contract catalog performance")
    parser.add_argument("--catalog-dir", default=str(SCRIPT_DIR / "catalog"))
    parser.add_argument("--group", default=os.environ.get("CONTRACT_GROUP", ""))
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--run-index", type=int, default=1)
    parser.add_argument("--run-total", type=int, default=1)
    parser.add_argument("--output", default="", help="Write run JSON to file")
    args = parser.parse_args()

    backend_url = os.environ.get("CONTRACT_BACKEND_URL", "https://localhost:4000").rstrip("/")
    path_prefix = os.environ.get("CONTRACT_PATH_PREFIX", "").rstrip("/")
    insecure = env_flag("CONTRACT_TLS_INSECURE", True)
    http_timeout = env_int("CONTRACT_HTTP_TIMEOUT", 60)

    cases = load_catalog(Path(args.catalog_dir), group=args.group or None)
    token = oc_token()
    probe_backend(backend_url, insecure, path_prefix)

    if not args.run_all:
        parser.error("--run-all is required")

    results = run_all_cases(
        cases,
        run_index=args.run_index,
        run_total=args.run_total,
        backend_url=backend_url,
        token=token,
        path_prefix=path_prefix,
        insecure=insecure,
        http_timeout=http_timeout,
    )
    payload = {
        "run_index": args.run_index,
        "cases": results,
        "total_elapsed_seconds": round_elapsed(sum(float(r["elapsed_seconds"]) for r in results)),
    }
    text = json.dumps(payload, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
