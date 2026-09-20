"""The Codex stdio hook adapter."""
from __future__ import annotations

import io
import json
import urllib.error

from bridge_for_agents import hook


def event(**extra):
    return {"hook_event_name": "PermissionRequest", "session_id": "thr_123", **extra}


def test_relay_writes_the_bridge_decision(monkeypatch):
    decision = {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow"}}}
    monkeypatch.setattr(hook, "_post", lambda body: (200, decision))
    stdout = io.StringIO()

    assert hook.relay(io.StringIO(json.dumps(event())), stdout, io.StringIO()) == 0
    assert json.loads(stdout.getvalue()) == decision


def test_relay_passes_the_complete_codex_event(monkeypatch):
    seen = {}

    def fake_post(body):
        seen.update(body)
        return 200, {}

    monkeypatch.setattr(hook, "_post", fake_post)
    payload = event(turn_id="turn_1", model="gpt-6", tool_name="Bash",
                    tool_input={"command": "pytest"})
    hook.relay(io.StringIO(json.dumps(payload)), io.StringIO(), io.StringIO())
    assert seen == payload


def test_bridge_failure_abstains_instead_of_inventing_a_decision(monkeypatch):
    monkeypatch.setattr(hook, "_post", lambda body: (0, {"error": "connection refused"}))
    stdout, stderr = io.StringIO(), io.StringIO()

    assert hook.relay(io.StringIO(json.dumps(event())), stdout, stderr) == 0
    assert stdout.getvalue() == ""
    assert "connection refused" in stderr.getvalue()


def test_malformed_input_abstains_safely():
    stdout, stderr = io.StringIO(), io.StringIO()
    assert hook.relay(io.StringIO("{broken"), stdout, stderr) == 0
    assert stdout.getvalue() == ""
    assert "invalid hook input" in stderr.getvalue()


def test_post_uses_the_shared_bridge_configuration(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.headers),
                    body=json.loads(request.data), timeout=timeout)
        return Response()

    monkeypatch.setenv("BRIDGE_URL", "https://bridge.example:8765/")
    monkeypatch.setenv("BRIDGE_TOKEN", "secret")
    monkeypatch.setenv("BRIDGE_HOOK_TIMEOUT", "42")
    monkeypatch.setattr(hook.urllib.request, "urlopen", fake_urlopen)

    assert hook._post(event()) == (200, {"ok": True})
    assert seen["url"] == "https://bridge.example:8765/hook"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["body"] == event()
    assert seen["timeout"] == 42


def test_http_error_is_returned_without_raising(monkeypatch):
    error = urllib.error.HTTPError("http://bridge/hook", 401, "Unauthorized", {},
                                  io.BytesIO(b'{"error":"unauthorized"}'))
    monkeypatch.setattr(
        hook.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(error)
    )
    assert hook._post(event()) == (401, {"error": "unauthorized"})
