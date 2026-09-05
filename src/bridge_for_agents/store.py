"""In-memory activity store behind the admin page.

Everything here lives in the process and nowhere else: restarting the bridge
starts the history over. That is deliberate for now — the events carry tool
inputs (commands, file paths, prompts), so not writing them to disk keeps the
blast radius of the bridge host small. If persistence is ever wanted, this
module is the single place to add it.

All state is touched from the daemon's single event loop, so no locking.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# The hook event currently being handled. Set by the HTTP endpoint so that
# Channel.ask — several frames down, with no access to the event — can still
# attribute a prompt to its session without changing the Channel API.
current_event: ContextVar[dict | None] = ContextVar("current_event", default=None)

# Set by a handler when the reason it gave up is worth distinguishing: a prompt
# nobody answered reads very differently from one deliberately sent back to the
# terminal, and both return {} to Claude Code.
outcome_hint: ContextVar[str | None] = ContextVar("outcome_hint", default=None)

_TAG = re.compile(r"<[^>]+>")


def plain(markup: str) -> str:
    """Telegram HTML back to something readable in a browser table."""
    import html as _html

    return _html.unescape(_TAG.sub("", markup)).strip()


@dataclass
class Event:
    """One hook request, from arrival to the decision returned to Claude Code."""

    id: int
    ts: float
    session_id: str
    event: str
    tool: str | None
    summary: str
    outcome: str | None = None
    duration_ms: int | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "session_id": self.session_id,
            "event": self.event,
            "tool": self.tool,
            "summary": self.summary,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Prompt:
    """A question on the phone that nobody has answered yet."""

    rid: str
    ts: float
    deadline: float
    session_id: str
    text: str
    options: list[str]

    def as_dict(self) -> dict:
        return {
            "rid": self.rid,
            "ts": self.ts,
            "deadline": self.deadline,
            "remaining": max(0, round(self.deadline - time.time())),
            "session_id": self.session_id,
            "text": self.text,
            "options": self.options,
        }


@dataclass
class Session:
    session_id: str
    cwd: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    ended: bool = False
    topic_id: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=200))

    @property
    def project(self) -> str:
        return self.cwd.rstrip("/").rsplit("/", 1)[-1] or "?"

    def as_dict(self, with_events: bool = False) -> dict:
        d = {
            "session_id": self.session_id,
            "short_id": self.session_id[:8],
            "cwd": self.cwd,
            "project": self.project,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "ended": self.ended,
            "topic_id": self.topic_id,
            "counts": self.counts,
            "total": sum(self.counts.values()),
        }
        if with_events:
            d["events"] = [e.as_dict() for e in reversed(self.events)]
        return d


class Store:
    """Bounded, ephemeral record of what the bridge has been doing."""

    def __init__(self, max_sessions: int = 50, max_events: int = 200,
                 max_feed: int = 500) -> None:
        self.started = time.time()
        self.max_sessions = max_sessions
        self.max_events = max_events
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.feed: deque[Event] = deque(maxlen=max_feed)
        self.prompts: dict[str, Prompt] = {}
        self._seq = 0

    # -- sessions ----------------------------------------------------------
    def session(self, ev: dict) -> Session:
        sid = ev.get("session_id") or "unknown"
        s = self.sessions.get(sid)
        if s is None:
            s = Session(session_id=sid, events=deque(maxlen=self.max_events))
            self.sessions[sid] = s
            while len(self.sessions) > self.max_sessions:
                self.sessions.popitem(last=False)  # drop the least recently active
        if cwd := ev.get("cwd"):
            s.cwd = cwd
        s.last_seen = time.time()
        self.sessions.move_to_end(sid)
        return s

    # -- events ------------------------------------------------------------
    def record(self, ev: dict, summary: str = "", topic_id: int | None = None) -> Event:
        s = self.session(ev)
        if topic_id is not None:
            s.topic_id = topic_id
        name = ev.get("hook_event_name") or "?"
        s.counts[name] = s.counts.get(name, 0) + 1
        self._seq += 1
        e = Event(
            id=self._seq,
            ts=time.time(),
            session_id=s.session_id,
            event=name,
            tool=ev.get("tool_name"),
            summary=summary,
        )
        s.events.append(e)
        self.feed.append(e)
        if name == "SessionEnd":
            s.ended = True
        return e

    def complete(self, e: Event, outcome: str) -> None:
        e.outcome = outcome
        e.duration_ms = round((time.time() - e.ts) * 1000)

    # -- pending prompts ---------------------------------------------------
    def open_prompt(self, rid: str, text: str, options: list[str], timeout: int) -> None:
        now = time.time()
        self.prompts[rid] = Prompt(
            rid=rid,
            ts=now,
            deadline=now + timeout,
            session_id=(current_event.get() or {}).get("session_id") or "unknown",
            text=plain(text)[:2000],
            options=options,
        )

    def close_prompt(self, rid: str) -> None:
        self.prompts.pop(rid, None)

    # -- views -------------------------------------------------------------
    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
        sessions = sorted(self.sessions.values(), key=lambda s: s.last_seen, reverse=True)
        selected = self.sessions.get(session_id) if session_id else None
        return {
            "now": time.time(),
            "started": self.started,
            "uptime": round(time.time() - self.started),
            "totals": {
                "sessions": len(self.sessions),
                "active": sum(1 for s in self.sessions.values() if not s.ended),
                "events": self._seq,
                "waiting": len(self.prompts),
            },
            "waiting": [p.as_dict() for p in
                        sorted(self.prompts.values(), key=lambda p: p.ts)],
            "sessions": [s.as_dict() for s in sessions],
            "selected": selected.as_dict(with_events=True) if selected else None,
            "feed": [e.as_dict() for e in reversed(self.feed)][:100],
        }
