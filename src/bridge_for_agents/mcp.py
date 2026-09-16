"""An MCP server exposing one tool: send the user a notification.

Typical use is a long-running loop reporting progress to your phone without
you watching the terminal.

    claude mcp add bridge-notify -- bridge-for-agents-mcp

The JSON-RPC is written out by hand rather than pulled from the MCP SDK. The
SDK brings roughly twenty-five transitive dependencies — pydantic, starlette,
uvicorn, cryptography, opentelemetry — for a stdio server with one tool, and
this project treats dependency surface as a security property. What is
implemented is the whole of what a one-tool server needs: `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`.

**The tool is send-only, and that is the point.** It calls the bridge's
`/notify`, which sends and returns; there is no reply path, nothing is awaited,
and nothing from the chat can reach the agent through it. See the invariant in
PLAN.md.

Configuration comes from the same environment as the bridge:

    BRIDGE_URL     default http://127.0.0.1:8765
    BRIDGE_TOKEN   the bearer token, when the bridge requires one
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, TextIO

NAME = "bridge-for-agents"
LATEST = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "notify_user",
        "title": "Notify the user",
        "description": (
            "Send the user a short push notification on their phone, through the "
            "bridge's chat channel. Use it to report progress from a long-running "
            "task, to say a job finished, or to flag something that needs their "
            "attention while they are away from the terminal.\n\n"
            "It is one-way: the user cannot reply to it, and nothing is returned "
            "except confirmation that it was sent. To ask a question, use "
            "AskUserQuestion, which the bridge also routes to the phone.\n\n"
            "Keep it to a line or two. Never include credentials, tokens, keys, "
            "file contents, or personal data — this leaves the machine and is "
            "stored by a third-party chat service."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "What to tell the user. One or two lines, plain text.",
                    "maxLength": 3000,
                },
                "level": {
                    "type": "string",
                    "enum": ["info", "warn", "error"],
                    "description": "info for progress, warn for something off, "
                                   "error for a failure that stopped the work.",
                    "default": "info",
                },
            },
            "required": ["message"],
        },
    }
]


def _post(path: str, body: dict, timeout: float = 15) -> tuple[int, dict]:
    url = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/") + path
    headers = {"content-type": "application/json"}
    if token := os.environ.get("BRIDGE_TOKEN", ""):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"error": e.reason}
    except Exception as e:
        return 0, {"error": str(e)}


def call_tool(name: str, args: dict) -> dict:
    """Returns a tools/call result. Failures come back as isError, not as
    protocol errors — the model should see them and can decide to carry on."""
    if name != "notify_user":
        raise KeyError(name)

    message = str(args.get("message") or "").strip()
    if not message:
        return _text("message is required", error=True)

    status, body = _post("/notify", {
        "message": message,
        "level": args.get("level", "info"),
        # Claude Code does not expose the session id to MCP servers — there is
        # no such environment variable — so this is almost always empty and the
        # bridge infers the session from the directory instead. Kept as an
        # override for other clients and for testing.
        "session_id": os.environ.get("BRIDGE_SESSION_ID")
                      or os.environ.get("CLAUDE_SESSION_ID", ""),
        # CLAUDE_PROJECT_DIR is what Claude Code does set, and it matches the
        # `cwd` the hooks report; getcwd can differ.
        "cwd": os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
    })
    if status == 200:
        hidden = body.get("redacted") or 0
        note = f" ({hidden} secret(s) redacted before sending)" if hidden else ""
        return _text(f"Notification sent to the user.{note}")
    if status == 0:
        return _text(f"Could not reach the bridge: {body.get('error')}. "
                     "Is it running, and is BRIDGE_URL right?", error=True)
    return _text(f"The bridge refused the notification ({status}): "
                 f"{body.get('error', 'unknown error')}", error=True)


def _text(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(req: dict) -> dict | None:
    """One JSON-RPC message in, one response out — or None for a notification."""
    method, rid = req.get("method"), req.get("id")

    if method == "initialize":
        asked = (req.get("params") or {}).get("protocolVersion")
        return _ok(rid, {
            "protocolVersion": asked if asked in SUPPORTED else LATEST,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NAME, "title": "Bridge for agents",
                           "version": _version()},
            "instructions": "notify_user sends the user a one-way push "
                            "notification. It cannot ask questions or read replies.",
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                                   # notifications get no reply

    if method == "ping":
        return _ok(rid, {})

    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params") or {}
        try:
            return _ok(rid, call_tool(params.get("name", ""), params.get("arguments") or {}))
        except KeyError as e:
            return _err(rid, -32602, f"Unknown tool: {e.args[0]}")
        except Exception as e:                        # never take the server down
            return _ok(rid, _text(f"Tool failed: {e}", error=True))

    if rid is None:
        return None                                   # unknown notification: ignore
    return _err(rid, -32601, f"Method not found: {method}")


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _version() -> str:
    from . import __version__

    return __version__


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Read newline-delimited JSON-RPC from stdin until it closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp: dict | None = _err(None, -32700, "Parse error")
        else:
            resp = handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
