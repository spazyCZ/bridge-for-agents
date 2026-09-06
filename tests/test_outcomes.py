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


# --- the AskUserQuestion contract ----------------------------------------
async def _answer(bridge, monkeypatch, reply):
    """Drive on_ask_user_question with one typed reply."""
    class Chan:
        async def ask(self, text, options, timeout, thread=None, hint=""):
            return ("text", reply)

    ev = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
          "tool_input": {"questions": [{
              "question": "Which database?", "header": "DB",
              "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}}
    return await bridge.on_ask_user_question(Chan(), ev, None)


async def test_answers_map_a_question_to_a_bare_option_label(bridge, monkeypatch):
    """The documented contract: `answers` maps question text to the selected
    option *label*. A decorated value is not a label."""
    out = (await _answer(bridge, monkeypatch, "2"))["hookSpecificOutput"]
    assert out["updatedInput"]["answers"] == {"Which database?": "SQLite"}
    assert out["permissionDecision"] == "allow"


async def test_the_original_questions_are_echoed_back(bridge, monkeypatch):
    """`allow` alone is not sufficient for AskUserQuestion; the input must come
    back whole with `answers` added."""
    out = (await _answer(bridge, monkeypatch, "1"))["hookSpecificOutput"]
    assert out["updatedInput"]["questions"][0]["question"] == "Which database?"
    assert len(out["updatedInput"]["questions"][0]["options"]) == 2


async def test_a_comment_travels_in_additional_context_not_in_the_answer(bridge, monkeypatch):
    out = (await _answer(bridge, monkeypatch, "2 - but check the migration first"))
    spec = out["hookSpecificOutput"]
    assert spec["updatedInput"]["answers"] == {"Which database?": "SQLite"}
    assert "check the migration first" in spec["additionalContext"]
    assert "check the migration" not in str(spec["updatedInput"])


async def test_no_comment_means_no_additional_context(bridge, monkeypatch):
    spec = (await _answer(bridge, monkeypatch, "SQLite"))["hookSpecificOutput"]
    assert "additionalContext" not in spec


async def test_free_text_that_names_no_option_is_still_the_answer(bridge, monkeypatch):
    """Not a label, but it is what the user said, and there is nothing better."""
    spec = (await _answer(bridge, monkeypatch, "neither, use DuckDB"))["hookSpecificOutput"]
    assert spec["updatedInput"]["answers"] == {"Which database?": "neither, use DuckDB"}
