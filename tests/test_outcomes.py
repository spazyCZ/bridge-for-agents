"""How a hook response is summarised for the admin feed."""
from __future__ import annotations

import pytest


def perm(behavior=None, message=None):
    if behavior is None:
        return {}
    d = {"behavior": behavior}
    if message:
        d["message"] = message
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


@pytest.mark.parametrize("out,hint,want", [
    (perm("allow"), None, "allow"),
    (perm("deny"), None, "deny"),
    (perm("deny", "not on production"), None, "deny (reason)"),
    ({}, "terminal", "terminal"),
    ({}, "timeout", "timeout"),      # nobody answered — different from choosing terminal
    ({}, None, "terminal"),
])
def test_permission_outcomes(bridge, out, hint, want):
    assert bridge.outcome_of("PermissionRequest", out, hint) == want


def test_question_outcome_counts_answers(bridge):
    out = {"hookSpecificOutput": {"updatedInput": {"answers": {"a": "1", "b": "2"}}}}
    assert bridge.outcome_of("PreToolUse", out, None) == "answered (2)"


def test_question_falls_back_to_the_hint(bridge):
    assert bridge.outcome_of("PreToolUse", {}, "timeout") == "timeout"


@pytest.mark.parametrize("name", ["Stop", "Notification", "SessionEnd"])
def test_fire_and_forget_events_are_sent(bridge, name):
    assert bridge.outcome_of(name, {}, None) == "sent"


def test_describe_uses_the_right_field_per_event(bridge):
    assert bridge.describe({"hook_event_name": "Stop",
                            "last_assistant_message": "  done  "}) == "done"
    assert bridge.describe({"hook_event_name": "Notification",
                            "message": "waiting"}) == "waiting"
    assert bridge.describe({"hook_event_name": "SessionEnd", "reason": "clear"}) == "clear"
    assert bridge.describe({"hook_event_name": "PermissionRequest", "tool_name": "Bash",
                            "tool_input": {"command": "ls -la"}}) == "ls -la"


def test_describe_shows_the_question_not_its_json(bridge):
    ev = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
          "tool_input": {"questions": [{"question": "Which database?"},
                                       {"question": "Which region?"}]}}
    assert bridge.describe(ev) == "Which database? · Which region?"


def test_api_base_is_configurable(bridge, monkeypatch):
    """The stand-in Telegram in bridge-for-agent-test depends on this."""
    monkeypatch.setattr(bridge, "TG_API_BASE", "http://127.0.0.1:8081")
    assert bridge.TelegramChannel("tok", -1).api == "http://127.0.0.1:8081/bottok"
