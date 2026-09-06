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


def test_it_shows_the_scope_and_the_answer_window(card):
    out = card(SCOPE="project", WAIT=300)
    assert "project" in out and "300s" in out


# --- what it must NOT say ------------------------------------------------
def test_it_does_not_publish_the_security_posture(bridge, card):
    """A chat is the wrong place to list which controls are off.

    The full fingerprint goes to the audit log's bridge_start record, which
    stays on the host.
    """
    out = card(TOKEN="", ADMIN=True, ADMIN_TOKEN="")
    for leak in ("hook auth", "TLS", "admin", "redaction", "audit", "OFF", "security"):
        assert leak not in out, f"{leak!r} is posture, not identity"


def test_it_does_not_publish_the_listener_address(bridge, card):
    """The bind, the port and http-vs-https all say something about exposure."""
    out = card(BIND="0.0.0.0", PORT=8765)
    assert "0.0.0.0" not in out and "8765" not in out
    assert "http" not in out


def test_the_card_carries_no_secrets(bridge, card, monkeypatch):
    """It goes to the chat. Identity yes, values never."""
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


# --- shutdown ------------------------------------------------------------
def test_shutdown_sets_the_stop_event(bridge):
    import asyncio
    import signal

    stop = asyncio.Event()
    bridge._shutdown(signal.SIGTERM, stop)
    assert stop.is_set()


def test_main_installs_handlers_for_both_stop_signals():
    """A regression guard, from five bridge lifetimes with zero clean stops.

    SIGTERM — what kill and systemd send — terminates Python where it stands
    unless a handler is installed, so the shutdown block never ran and no
    offline notice was ever sent. Only Ctrl-C would have unwound it, which is
    not how anything stops a daemon.
    """
    import inspect

    from bridge_for_agents import bridge as b

    src = inspect.getsource(b.main)
    assert "signal.SIGTERM" in src
    assert "signal.SIGINT" in src
    assert "add_signal_handler" in src
    assert "await stop.wait()" in src, "must wait on the event the handlers set"
