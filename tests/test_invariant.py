"""The invariant: nothing the chat sends can start anything.

An inbound update either answers a request that is currently open, or it is
dropped and recorded. These tests are the executable statement of that — if one
of them starts failing, the property is gone.
"""
from __future__ import annotations

import asyncio

import pytest

from bridge_for_agents.store import Store

CHAT = -100
OPTIONS = [("✅ Allow", "allow"), ("❌ Deny", "deny")]


@pytest.fixture
def chan(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "STORE", Store())
    c = bridge.TelegramChannel("token", CHAT)
    calls: list[tuple] = []

    async def fake_send(text, thread=None, **kw):
        calls.append(("send", text))
        return {"message_id": 42}

    async def fake_call(method, **params):
        calls.append((method, params))
        return None

    monkeypatch.setattr(c, "send", fake_send)
    monkeypatch.setattr(c, "call", fake_call)
    c.calls = calls
    c.store = bridge.STORE
    return c


def msg(text, chat=CHAT, reply_to=None):
    m = {"message_id": 7, "chat": {"id": chat}, "text": text}
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to, "chat": {"id": chat}}
    return {"message": m}


def cb(data, chat=CHAT, message_id=42):
    return {"callback_query": {"id": "cq1", "data": data,
                               "message": {"message_id": message_id,
                                           "chat": {"id": chat}}}}


async def _open_prompt(chan):
    task = asyncio.create_task(chan.ask("prompt", OPTIONS, 5))
    for _ in range(100):
        if chan.pending:
            return task, next(iter(chan.pending))
        await asyncio.sleep(0.005)
    raise AssertionError("prompt never opened")


# --- nothing open: every update is dropped -------------------------------
@pytest.mark.parametrize("update,kind", [
    (msg("hello"), "message"),
    (msg("/ping"), "message"),
    (msg("/pending"), "message"),
    (msg("run the tests again"), "message"),
    (msg("anything", reply_to=999), "message"),      # reply to an unknown message
    (cb("deadbeef:allow"), "callback"),              # stale or forged callback
])
async def test_unsolicited_updates_are_dropped_and_recorded(chan, update, kind):
    await chan._on_update(update)
    assert chan.store.snapshot()["totals"]["rejected"] == 1
    assert chan.store.snapshot()["rejected"][0]["kind"] == kind
    assert not any(c[0] == "send" for c in chan.calls), "replied to an unsolicited update"


async def test_ping_and_pending_are_gone(chan):
    """They were chat-initiated actions. Their absence is the point."""
    for text in ("/ping", "/pending", "/start", "/help"):
        await chan._on_update(msg(text))
    assert not any(c[0] == "send" for c in chan.calls)
    assert chan.store.snapshot()["totals"]["rejected"] == 4


async def test_updates_from_another_chat_are_ignored_entirely(chan):
    await chan._on_update(msg("hello", chat=-999))
    await chan._on_update(cb("x:allow", chat=-999))
    assert chan.store.snapshot()["totals"]["rejected"] == 0   # not even recorded
    assert not any(c[0] == "send" for c in chan.calls)


# --- with something open: only the matching request resolves -------------
async def test_a_matching_button_resolves_its_own_request(chan):
    task, rid = await _open_prompt(chan)
    await chan._on_update(cb(f"{rid}:allow"))
    assert await task == ("button", "allow")
    assert chan.store.snapshot()["totals"]["rejected"] == 0


async def test_a_button_for_a_different_request_is_dropped(chan):
    task, rid = await _open_prompt(chan)
    await chan._on_update(cb("00000000:allow"))
    assert chan.store.snapshot()["totals"]["rejected"] == 1
    assert not task.done(), "an unrelated callback resolved an open request"
    chan._resolve(rid, ("button", "deny"))
    await task


async def test_free_text_must_reply_to_the_open_prompt(chan):
    task, rid = await _open_prompt(chan)
    await chan._on_update(msg("not on production"))            # no reply_to
    assert chan.store.snapshot()["totals"]["rejected"] == 1
    assert not task.done()
    await chan._on_update(msg("not on production", reply_to=42))   # replies to it
    assert await task == ("text", "not on production")


async def test_a_second_answer_after_resolution_is_dropped(chan):
    task, rid = await _open_prompt(chan)
    await chan._on_update(cb(f"{rid}:allow"))
    await task
    await chan._on_update(cb(f"{rid}:deny"))
    assert chan.store.snapshot()["totals"]["rejected"] == 1


# --- the structural claim -------------------------------------------------
def test_on_update_has_exactly_one_acceptance_path():
    """A guard against reintroducing a branch by accident.

    `_on_update` must reach `_resolve` through a single guarded path. If a
    future change adds another way in, this fails and the reviewer has to
    justify it against the invariant.
    """
    import inspect

    from bridge_for_agents import bridge as b

    src = inspect.getsource(b.TelegramChannel._on_update)
    assert src.count("_resolve(") == 1
    assert src.count("_drop(") == 2          # the two rejection paths
    assert "elif" not in src
