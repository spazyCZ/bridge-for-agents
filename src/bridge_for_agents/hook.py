"""Stdio-to-HTTP hook adapter for Codex.

Codex command hooks receive one JSON event on stdin and expect their decision
on stdout.  The bridge already speaks the same event and decision shapes over
HTTP, so this command only transports the JSON between them:

    Codex -> stdin -> bridge-for-agents-hook -> POST /hook -> stdout -> Codex

Transport failures deliberately produce no decision.  Codex then uses its
normal local approval flow, preserving the bridge's fail-safe behavior.

Configuration:

    BRIDGE_URL           bridge root, default http://127.0.0.1:8765
    BRIDGE_TOKEN         bearer token, when the bridge requires one
    BRIDGE_HOOK_TIMEOUT  HTTP timeout in seconds, default 570
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, TextIO

DEFAULT_TIMEOUT = 570.0


def _timeout() -> float:
    try:
        value = float(os.environ.get("BRIDGE_HOOK_TIMEOUT", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _post(event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    url = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/") + "/hook"
    headers = {"content-type": "application/json"}
    if token := os.environ.get("BRIDGE_TOKEN", ""):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, json.dumps(event).encode(), headers)
    try:
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            body = json.loads(response.read() or b"{}")
            return response.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read() or b"{}")
        except Exception:
            body = {"error": error.reason}
        return error.code, body if isinstance(body, dict) else {}
    except Exception as error:
        return 0, {"error": str(error)}


def relay(stdin: TextIO | None = None, stdout: TextIO | None = None,
          stderr: TextIO | None = None) -> int:
    """Relay one Codex hook event, abstaining safely on every error."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        event = json.load(stdin)
    except (OSError, json.JSONDecodeError) as error:
        print(f"bridge-for-agents-hook: invalid hook input: {error}", file=stderr)
        return 0
    if not isinstance(event, dict) or not event.get("hook_event_name"):
        print("bridge-for-agents-hook: hook input has no hook_event_name", file=stderr)
        return 0

    status, body = _post(event)
    if status != 200:
        detail = body.get("error", "unknown error")
        target = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8765")
        print(f"bridge-for-agents-hook: bridge at {target} returned {status}: {detail}",
              file=stderr)
        return 0

    json.dump(body, stdout, separators=(",", ":"))
    stdout.write("\n")
    stdout.flush()
    return 0


def main() -> int:
    return relay()


if __name__ == "__main__":
    raise SystemExit(main())
