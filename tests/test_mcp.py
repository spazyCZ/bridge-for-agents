"""The MCP server: protocol shape, and that the tool stays one-way."""
from __future__ import annotations

import io
import json

import pytest

from bridge_for_agents import mcp


def rpc(method, params=None, rid=1):
    m = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        m["id"] = rid
    if params is not None:
        m["params"] = params
    return m


# --- handshake ------------------------------------------------------------
def test_initialize_declares_tools_and_echoes_a_supported_version():
    r = mcp.handle(rpc("initialize", {"protocolVersion": "2025-06-18"}))
    assert r["jsonrpc"] == "2.0" and r["id"] == 1
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert res["serverInfo"]["name"] == mcp.NAME
    assert res["serverInfo"]["version"]


def test_an_unknown_version_falls_back_to_ours_rather_than_erroring():
    r = mcp.handle(rpc("initialize", {"protocolVersion": "1.0.0"}))
    assert r["result"]["protocolVersion"] == mcp.LATEST


@pytest.mark.parametrize("version", mcp.SUPPORTED)
def test_every_advertised_version_is_echoed(version):
    r = mcp.handle(rpc("initialize", {"protocolVersion": version}))
    assert r["result"]["protocolVersion"] == version


def test_notifications_get_no_reply():
    assert mcp.handle(rpc("notifications/initialized", rid=None)) is None
    assert mcp.handle(rpc("notifications/cancelled", rid=None)) is None


def test_ping_is_answered():
    assert mcp.handle(rpc("ping"))["result"] == {}


def test_an_unknown_method_is_a_protocol_error():
    r = mcp.handle(rpc("resources/list"))
    assert r["error"]["code"] == -32601


def test_an_unknown_notification_is_ignored_silently():
    assert mcp.handle(rpc("notifications/whatever", rid=None)) is None


# --- the tool -------------------------------------------------------------
def test_tools_list_exposes_exactly_one_send_only_tool():
    tools = mcp.handle(rpc("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["notify_user"]
    schema = tools[0]["inputSchema"]
    assert schema["required"] == ["message"]
    assert schema["properties"]["level"]["enum"] == ["info", "warn", "error"]


def test_the_description_tells_the_model_it_cannot_get_a_reply():
    d = mcp.handle(rpc("tools/list"))["result"]["tools"][0]["description"]
    assert "one-way" in d
    assert "AskUserQuestion" in d          # points at the right tool for questions
    assert "credentials" in d


def test_calling_an_unknown_tool_is_a_protocol_error():
    r = mcp.handle(rpc("tools/call", {"name": "run_command", "arguments": {}}))
    assert r["error"]["code"] == -32602
    assert "run_command" in r["error"]["message"]


def test_an_empty_message_is_a_tool_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(mcp, "_post", lambda *a, **k: (200, {"ok": True}))
    r = mcp.handle(rpc("tools/call", {"name": "notify_user", "arguments": {"message": "  "}}))
    assert r["result"]["isError"] is True


def test_a_sent_notification_reports_success(monkeypatch):
    seen = {}

    def fake_post(path, body, timeout=15):
        seen.update(path=path, body=body)
        return 200, {"ok": True, "redacted": 0}

    monkeypatch.setattr(mcp, "_post", fake_post)
    r = mcp.handle(rpc("tools/call", {"name": "notify_user",
                                      "arguments": {"message": "build done", "level": "info"}}))
    assert r["result"]["isError"] is False
    assert "sent" in r["result"]["content"][0]["text"]
    assert seen["path"] == "/notify"
    assert seen["body"]["message"] == "build done"


def test_redaction_is_reported_back_to_the_model(monkeypatch):
    monkeypatch.setattr(mcp, "_post", lambda *a, **k: (200, {"ok": True, "redacted": 2}))
    r = mcp.handle(rpc("tools/call", {"name": "notify_user", "arguments": {"message": "x"}}))
    assert "2 secret(s) redacted" in r["result"]["content"][0]["text"]


@pytest.mark.parametrize("status,body,expect", [
    (0, {"error": "Connection refused"}, "Could not reach the bridge"),
    (401, {"error": "unauthorized"}, "refused"),
    (429, {"error": "rate limit: 20/min"}, "rate limit"),
    (403, {"error": "notifications are disabled"}, "disabled"),
])
def test_bridge_failures_come_back_as_tool_errors(monkeypatch, status, body, expect):
    """isError, not a protocol error: the model should see it and carry on."""
    monkeypatch.setattr(mcp, "_post", lambda *a, **k: (status, body))
    r = mcp.handle(rpc("tools/call", {"name": "notify_user", "arguments": {"message": "x"}}))
    assert r["result"]["isError"] is True
    assert expect in r["result"]["content"][0]["text"]


def test_an_unexpected_exception_does_not_kill_the_server(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp, "_post", boom)
    r = mcp.handle(rpc("tools/call", {"name": "notify_user", "arguments": {"message": "x"}}))
    assert r["result"]["isError"] is True
    assert "kaboom" in r["result"]["content"][0]["text"]


def test_there_is_no_tool_that_reads_from_the_chat():
    """The invariant, as a test: nothing here can pull chat input to the agent."""
    names = [t["name"] for t in mcp.TOOLS]
    assert names == ["notify_user"]
    blob = json.dumps(mcp.TOOLS).lower()
    for forbidden in ("reply", "read_messages", "await", "poll", "receive", "ask_user"):
        assert forbidden not in names, forbidden
    assert "one-way" in blob


# --- the stdio loop -------------------------------------------------------
def test_serve_reads_lines_and_writes_one_response_each():
    stdin = io.StringIO("\n".join([
        json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"})),
        json.dumps(rpc("notifications/initialized", rid=None)),
        json.dumps(rpc("tools/list", rid=2)),
        "",
    ]))
    out = io.StringIO()
    assert mcp.serve(stdin, out) == 0
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert [m["id"] for m in lines] == [1, 2]      # the notification produced nothing


def test_malformed_json_gets_a_parse_error_and_the_loop_continues():
    stdin = io.StringIO("{not json\n" + json.dumps(rpc("ping", rid=9)) + "\n")
    out = io.StringIO()
    mcp.serve(stdin, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["id"] == 9
