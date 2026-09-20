"""An outstanding prompt must appear on the admin page and disappear again.

Exercises the real TelegramChannel.ask, with only the network stubbed out.
"""
from __future__ import annotations

import asyncio

import pytest

from bridge_for_agents.store import Store

OPTIONS = [("✅ Allow", "allow"), ("❌ Deny", "deny")]


@pytest.fixture
def chan(bridge, monkeypatch):
    """A channel whose Telegram calls are stubbed, on a fresh store."""
    monkeypatch.setattr(bridge, "STORE", Store())
    c = bridge.TelegramChannel("token", -100)
    sent: list[str] = []

    async def fake_send(text, thread=None, **kw):
        sent.append(text)
        return {"message_id": 42}

    async def fake_call(method, **params):
        return None

    monkeypatch.setattr(c, "send", fake_send)
    monkeypatch.setattr(c, "call", fake_call)
    c.sent = sent
    c.store = bridge.STORE
    return c


async def _wait_for_prompt(chan):
    for _ in range(100):
        if chan.store.prompts:
            return next(iter(chan.store.prompts))
        await asyncio.sleep(0.005)
    raise AssertionError("prompt never registered")


async def test_prompt_is_visible_while_outstanding_then_cleared_on_answer(chan):
    task = asyncio.create_task(chan.ask("🔐 <b>Bash</b>\nls -la", OPTIONS, 5))
    rid = await _wait_for_prompt(chan)

    snap = chan.store.snapshot()
    assert snap["totals"]["waiting"] == 1
    w = snap["waiting"][0]
    assert w["text"] == "🔐 Bash\nls -la"           # markup stripped for the browser
    assert w["options"] == ["✅ Allow", "❌ Deny"]
    assert w["remaining"] > 0

    chan._resolve(rid, ("button", "allow"))
    assert await task == ("button", "allow")
    assert chan.store.snapshot()["totals"]["waiting"] == 0


async def test_prompt_is_cleared_on_timeout(chan):
    assert await chan.ask("🔐 <b>Write</b>\n/etc/passwd", OPTIONS, 0) is None
    assert chan.store.snapshot()["totals"]["waiting"] == 0


async def test_session_topic_cleanup_contains_transport_failures(bridge, chan, monkeypatch):
    """SessionEnd cleanup runs in a background task and must handle failures."""
    monkeypatch.setattr(bridge, "SCOPE", "session")
    event = {"session_id": "codex-live", "cwd": "/repo"}
    key, _ = chan._key(event)
    chan.is_forum = True
    chan.topics[key] = 74
    monkeypatch.setattr(chan, "_save_topics", lambda: None)

    async def failed_send(*args, **kwargs):
        raise TimeoutError("Telegram did not answer")

    async def failed_call(*args, **kwargs):
        raise TimeoutError("Telegram did not answer")

    monkeypatch.setattr(chan, "send", failed_send)
    monkeypatch.setattr(chan, "call", failed_call)

    await chan.close_thread(event)

    assert key not in chan.topics
