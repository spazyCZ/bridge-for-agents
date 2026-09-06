"""The startup card: which instance is this, and how is it configured.

Several bridges post into one group — one per machine is the recommended
shape — so "online" on its own tells you nothing when a message arrives from
somewhere you did not expect.
"""
from __future__ import annotations

import socket

import pytest


class FakeChan:
    username = "test_bot"
    is_forum = True


@pytest.fixture
def card(bridge, monkeypatch):
    def build(ssl_ctx=None, **cfg):
        for k, v in cfg.items():
            monkeypatch.setattr(bridge, k, v)
        return bridge.startup_card(FakeChan(), ssl_ctx)
    return build


def test_it_names_the_machine_and_the_bot(card):
    out = card()
    assert socket.gethostname() in out
    assert "@test_bot" in out


def test_it_names_the_chat_and_whether_it_is_a_forum(bridge, card):
    assert str(bridge.TG_CHAT_ID) in card()
    assert "forum" in card()


def test_it_shows_where_the_bridge_listens(card):
    assert "http://127.0.0.1:8765" in card(BIND="127.0.0.1", PORT=8765)


def test_tls_changes_the_scheme(card):
    assert "https://" in card(ssl_ctx=object(), BIND="0.0.0.0", PORT=8765)


def test_it_shows_the_scope_and_the_answer_window(card):
    out = card(SCOPE="project", WAIT=300)
    assert "project" in out and "300s" in out


# --- the posture line: OFF must be visible, not implied ------------------
def test_an_open_configuration_says_so_in_capitals(bridge, card):
    """Capitalised OFF, because this is the line you scan for a mistake."""
    out = card(TOKEN="", ADMIN=True, ADMIN_TOKEN="")
    assert "hook auth OFF" in out
    assert "TLS OFF" in out
    assert "admin on (no token)" in out


def test_a_locked_down_configuration_reads_as_such(bridge, card, monkeypatch):
    from bridge_for_agents.audit import AuditLog
    monkeypatch.setattr(bridge, "AUDIT", AuditLog("/tmp/x.jsonl", "k" * 64))
    out = card(ssl_ctx=object(), TOKEN="t" * 32, ADMIN=True, ADMIN_TOKEN="a" * 32)
    assert "hook auth on" in out
    assert "TLS on" in out
    assert "admin on" in out and "(no token)" not in out
    assert "audit chained" in out


def test_redaction_off_is_flagged(bridge, card, monkeypatch):
    from bridge_for_agents.redact import Redactor
    monkeypatch.setattr(bridge, "REDACTOR", Redactor(enabled=False))
    assert "redaction OFF" in card()


def test_no_audit_is_flagged(bridge, card, monkeypatch):
    from bridge_for_agents.audit import AuditLog
    monkeypatch.setattr(bridge, "AUDIT", AuditLog(None))
    assert "audit OFF" in card()


def test_the_card_carries_no_secrets(bridge, card, monkeypatch):
    """It goes to the chat. Posture yes, values never."""
    monkeypatch.setattr(bridge, "TG_BOT_TOKEN", "123456:SUPERSECRETTOKENVALUE")
    out = card(TOKEN="hooktokenhooktokenhooktoken12345", ADMIN_TOKEN="admintoken" * 3)
    assert "SUPERSECRETTOKENVALUE" not in out
    assert "hooktoken" not in out
    assert "admintoken" not in out


# --- two bridges, one token ----------------------------------------------
async def test_a_409_warns_the_chat_once(bridge, monkeypatch):
    """The misconfiguration that is wrong rather than broken: both pollers
    take updates at random, so an answer lands on whichever polled first."""
    import asyncio

    from bridge_for_agents.store import Store
    monkeypatch.setattr(bridge, "STORE", Store())
    chan = bridge.TelegramChannel("token", -100)
    chan.username = "test_bot"
    sent: list[str] = []

    async def fake_send(text, thread=None, **kw):
        sent.append(text)
        return {"message_id": 1}

    class FakeResp:
        async def json(self):
            return {"ok": False, "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request"}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakeHTTP:
        def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr(chan, "send", fake_send)
    chan.http = FakeHTTP()

    assert await chan.call("getUpdates") is None
    assert await chan.call("getUpdates") is None      # still conflicting
    await asyncio.sleep(0.05)                          # the warning is a task

    assert len(sent) == 1, "warned more than once"
    assert "Another bridge is polling" in sent[0]
    assert "@test_bot" in sent[0]
