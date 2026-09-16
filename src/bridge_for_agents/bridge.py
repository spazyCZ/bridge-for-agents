#!/usr/bin/env python3
"""
claude-bridge

A one-way approval channel: Claude Code asks, the phone answers.

    THE INVARIANT
    Every message to the phone originates from a Claude Code hook. Nothing the
    phone sends can start anything — it can only answer a request that is
    already open and waiting.

Everything here either enforces that, records it, or was left out for
conflicting with it. The omissions are deliberate: there is no way to inject a
prompt into a session, start one, resume one, run a command, or query state.
`_on_update` is the only inbound path and the only place the invariant could
be broken; it has one guard and no other branch. Adding a chat-initiated
command there — however harmless — is what makes the invariant negotiable.

Relays Claude Code hook events to a chat channel (Telegram for now) and
returns the user's decision back to Claude Code.

Flow:
  Claude Code --HTTP hook POST--> bridge --Telegram--> phone
  phone --button / reply--> bridge --JSON hook response--> Claude Code

Handled events:
  PermissionRequest            -> Allow / Deny / Terminal buttons
  PreToolUse (AskUserQuestion) -> option buttons + free-text reply
  Notification (idle_prompt)   -> "waiting for input" ping
  Stop                         -> last assistant message summary
  SessionEnd                   -> closes the session's topic

Session separation: each session (or project) gets its own forum topic in a
Telegram supergroup with Topics enabled. Falls back to a single flat chat
with inline tags if the chat is not a forum.

Env:
  TG_BOT_TOKEN   bot token from @BotFather
  TG_CHAT_ID     numeric id of the supergroup / chat (only this chat is trusted)
  TG_API_BASE    Bot API root, default https://api.telegram.org. Point it at a
                 stand-in server to exercise the bridge without a real bot
  BRIDGE_SCOPE   session | project | flat    (default: session)
                 session = one topic per Claude Code session
                 project = one topic per working directory, sessions share it
                 flat    = no topics, everything in one thread
  BRIDGE_STATE   topic map file, default ~/.local/state/claude-bridge/topics.json
  BRIDGE_BIND    interface to listen on, default 127.0.0.1
                 set to the LAN address (or 0.0.0.0) when Claude Code runs
                 on another host in your private network
  BRIDGE_TOKEN   shared secret; every hook request must carry
                 `Authorization: Bearer <token>`. Mandatory unless the
                 listener is on 127.0.0.1 or BRIDGE_INSECURE=1
  BRIDGE_TLS_CERT / BRIDGE_TLS_KEY
                 PEM server cert and key; when set the listener speaks HTTPS.
                 Generate with ./make-certs.sh
  BRIDGE_INSECURE  set to 1 to allow a non-loopback listener with no token
                 or no TLS (lab use only)
  BRIDGE_PORT    default 8765
  BRIDGE_TIMEOUT seconds to wait for a phone answer, default 540
                 (must stay below the hook `timeout` in settings.json)
  BRIDGE_ADMIN   set to 1 to serve the read-only web admin page at /admin
  BRIDGE_ADMIN_TOKEN
                 bearer token for /admin; falls back to BRIDGE_TOKEN. Mandatory
                 when the admin page is enabled on a non-loopback listener
  BRIDGE_ADMIN_HISTORY
                 events kept in memory per session, default 200. History is
                 never written to disk and is lost on restart
  BRIDGE_REDACT  set to 0 to send tool inputs to the chat unredacted. On by
                 default: recognisable credentials are replaced before the
                 text leaves the host. Best effort, never a guarantee
  BRIDGE_REDACT_EXTRA
                 path to a file of extra regexes, one per line, # for comments
  BRIDGE_AUDIT   append-only JSONL record of every request and decision,
                 default ~/.local/state/claude-bridge/audit.jsonl, mode 0600.
                 Set to `off` to disable. Holds the FULL tool input, unredacted
  BRIDGE_AUDIT_KEY
                 when set, each record is chained with an HMAC so an edited or
                 deleted line is detectable. Keep it away from the log itself
  BRIDGE_LOG_LEVEL
                 DEBUG | INFO | WARNING | ERROR, default INFO
  BRIDGE_LOG_FILE
                 also write the diagnostic log here, mode 0600, rotating at
                 10 MB with 5 kept. Secrets are redacted from it, unlike the
                 audit log
  BRIDGE_LOG_ACCESS
                 set to 1 for aiohttp's per-request access log. Off by default:
                 the admin page polls every 2 s and would drown everything else
  BRIDGE_NOTIFY  set to 0 to refuse agent-sent notifications on POST /notify
  BRIDGE_NOTIFY_RATE
                 most notifications accepted per minute, default 20. A runaway
                 loop should not be able to flood your phone
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
import json
import logging
import os
import signal
import socket
import ssl
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from . import __version__, admin, audit, logs, redact, replies, store

log = logging.getLogger("bridge")

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = int(os.environ["TG_CHAT_ID"])
TG_API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/")
SCOPE = os.environ.get("BRIDGE_SCOPE", "session")
STATE = Path(os.environ.get("BRIDGE_STATE",
             Path.home() / ".local/state/claude-bridge/topics.json"))
BIND = os.environ.get("BRIDGE_BIND", "127.0.0.1")
TOKEN = os.environ.get("BRIDGE_TOKEN", "")
TLS_CERT = os.environ.get("BRIDGE_TLS_CERT", "")
TLS_KEY = os.environ.get("BRIDGE_TLS_KEY", "")
INSECURE = os.environ.get("BRIDGE_INSECURE") == "1"
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
WAIT = int(os.environ.get("BRIDGE_TIMEOUT", "540"))
LOG_LEVEL = os.environ.get("BRIDGE_LOG_LEVEL", "INFO")
LOG_FILE = os.environ.get("BRIDGE_LOG_FILE", "")
LOG_ACCESS = os.environ.get("BRIDGE_LOG_ACCESS") == "1"
NOTIFY = os.environ.get("BRIDGE_NOTIFY", "1") != "0"
NOTIFY_RATE = int(os.environ.get("BRIDGE_NOTIFY_RATE", "20"))
ADMIN = os.environ.get("BRIDGE_ADMIN") == "1"
ADMIN_TOKEN = os.environ.get("BRIDGE_ADMIN_TOKEN", "") or TOKEN
HISTORY = int(os.environ.get("BRIDGE_ADMIN_HISTORY", "200"))
_audit_path = os.environ.get("BRIDGE_AUDIT", "")
AUDIT = audit.AuditLog(
    None if _audit_path == "off" else (_audit_path or audit.DEFAULT_PATH),
    os.environ.get("BRIDGE_AUDIT_KEY", ""),
)
REDACTOR = redact.Redactor(
    enabled=os.environ.get("BRIDGE_REDACT", "1") != "0",
    extra_path=os.environ.get("BRIDGE_REDACT_EXTRA", ""),
)

Answer = tuple[str, str]  # ("button", value) | ("text", value)

# Everything the admin page shows. In memory only — see store.py.
STORE = store.Store(max_events=HISTORY)


# --------------------------------------------------------------------------
# Channel abstraction — implement this for WhatsApp/Twilio etc. later
# --------------------------------------------------------------------------
class Channel:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def thread_for(self, ev: dict) -> Any:
        """Return an opaque thread handle for this hook event, or None."""
        return None

    async def close_thread(self, ev: dict) -> None: ...

    async def send(self, text: str, thread: Any = None) -> None:
        raise NotImplementedError

    async def ask(self, text: str, options: list[tuple[str, str]], timeout: int,
                  thread: Any = None, hint: str = "") -> Answer | None:
        """Show `text` with `options` [(label, value)], wait for a button press
        or a free-text reply. `hint` tells the user what typing will do.
        Returns None on timeout."""
        raise NotImplementedError


class TelegramChannel(Channel):
    def __init__(self, token: str, chat_id: int):
        self.api = f"{TG_API_BASE}/bot{token}"
        self.chat_id = chat_id
        self.pending: dict[str, asyncio.Future] = {}
        self.msg_to_rid: dict[int, str] = {}
        self.topics: dict[str, int] = {}          # scope key -> message_thread_id
        self.topic_locks: dict[str, asyncio.Lock] = {}
        self.is_forum = False
        self.username = ""
        self.conflict_warned = False
        self.http: ClientSession | None = None
        self._poll_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        # trust_env: pick up HTTPS_PROXY / NO_PROXY from the environment
        self.http = ClientSession(timeout=ClientTimeout(total=45), trust_env=True)
        await self.call("deleteWebhook")  # long polling needs no webhook
        me = await self.call("getMe") or {}
        self.username = me.get("username", "?")
        chat = await self.call("getChat", chat_id=self.chat_id) or {}
        self.is_forum = bool(chat.get("is_forum"))
        if SCOPE != "flat" and not self.is_forum:
            log.warning("chat %s is not a forum supergroup — falling back to flat mode. "
                        "Enable Topics in the group settings for per-%s threads",
                        self.chat_id, SCOPE)
        self._load_topics()
        self._poll_task = asyncio.create_task(self._poll())
        log.info("telegram channel started (forum=%s scope=%s)", self.is_forum, SCOPE)

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        if self.http:
            await self.http.close()

    # -- topic state -------------------------------------------------------
    def _load_topics(self) -> None:
        try:
            self.topics = json.loads(STATE.read_text())
            log.info("loaded %d topic mappings from %s", len(self.topics), STATE)
        except FileNotFoundError:
            self.topics = {}
        except Exception:
            log.exception("could not read %s — starting with an empty topic map", STATE)
            self.topics = {}

    def _save_topics(self) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.topics))
            tmp.replace(STATE)
        except Exception:
            log.exception("could not persist topic map to %s", STATE)

    @staticmethod
    def _key(ev: dict) -> tuple[str, str]:
        """(state key, human topic name) for the configured scope."""
        cwd = os.path.basename((ev.get("cwd") or "").rstrip("/")) or "claude"
        sid = ev.get("session_id") or "unknown"
        if SCOPE == "project":
            return f"project:{ev.get('cwd')}", cwd
        return f"session:{sid}", f"{cwd} · {sid[:8]}"

    async def thread_for(self, ev: dict) -> int | None:
        if SCOPE == "flat" or not self.is_forum:
            return None
        key, name = self._key(ev)
        if tid := self.topics.get(key):
            return tid
        lock = self.topic_locks.setdefault(key, asyncio.Lock())
        async with lock:  # concurrent hooks from one session create one topic
            if tid := self.topics.get(key):
                return tid
            res = await self.call("createForumTopic", chat_id=self.chat_id, name=name[:128])
            if not res:
                log.warning("createForumTopic failed for %s — using the general thread", name)
                return None
            tid = res["message_thread_id"]
            self.topics[key] = tid
            self._save_topics()
            log.info("created topic %s for %s", tid, key)
            return tid

    async def close_thread(self, ev: dict) -> None:
        if SCOPE != "session" or not self.is_forum:
            return
        key, _ = self._key(ev)
        tid = self.topics.pop(key, None)
        if tid is None:
            return
        self._save_topics()
        await self.send("🔒 session ended", thread=tid)
        await self.call("closeForumTopic", chat_id=self.chat_id, message_thread_id=tid)

    # -- transport ---------------------------------------------------------
    async def call(self, method: str, **params: Any) -> Any:
        assert self.http
        params = {k: v for k, v in params.items() if v is not None}
        log.debug("telegram -> %s %s", method, {k: v for k, v in params.items()
                                                 if k not in ("text", "reply_markup")})
        async with self.http.post(f"{self.api}/{method}", json=params) as r:
            data = await r.json()
        if not data.get("ok"):
            if data.get("error_code") == 409:
                # Two bridges are polling this bot token. They steal each
                # other's updates at random, so a button press lands on
                # whichever polled first — wrong rather than broken.
                log.error("CONFLICT: another bridge is polling this bot token. "
                          "One token, one process — see the README on deployment.")
                if not self.conflict_warned:
                    self.conflict_warned = True
                    asyncio.create_task(self.send(
                        f"⚠️ <b>Another bridge is polling @{esc(self.username)}</b>\n"
                        f"Two processes share this bot token, so answers will land on "
                        f"whichever polled first. Stop one of them.\n"
                        f"<code>{esc(socket.gethostname())}</code> is one of them."))
            else:
                log.warning("telegram %s failed: %s", method, data.get("description"))
            return None
        return data.get("result")

    async def send(self, text: str, thread: Any = None, **kw: Any) -> Any:
        return await self.call(
            "sendMessage", chat_id=self.chat_id, message_thread_id=thread,
            text=text[:4000], parse_mode="HTML", disable_web_page_preview=True, **kw,
        )

    async def ask(self, text: str, options: list[tuple[str, str]], timeout: int,
                  thread: Any = None, hint: str = "") -> Answer | None:
        rid = uuid.uuid4().hex[:8]
        log.debug("asking %s (%d options, %ds) in thread %s", rid, len(options), timeout, thread)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        STORE.open_prompt(rid, text, [lbl for lbl, _ in options], timeout)
        keyboard = {"inline_keyboard": [[{"text": lbl, "callback_data": f"{rid}:{val}"}]
                                        for lbl, val in options]}
        hint = hint or "Tap a button or reply to this message."
        msg = await self.send(f"{text}\n\n<i>{hint}</i>",
                              thread=thread, reply_markup=keyboard)
        if msg:
            self.msg_to_rid[msg["message_id"]] = rid
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            if msg:
                await self._finalize(msg["message_id"], "⌛ timed out → terminal")
            return None
        finally:
            self.pending.pop(rid, None)
            STORE.close_prompt(rid)
            if msg:
                self.msg_to_rid.pop(msg["message_id"], None)

    def _resolve(self, rid: str, answer: Answer) -> bool:
        fut = self.pending.get(rid)
        if fut and not fut.done():
            fut.set_result(answer)
            return True
        return False

    async def _finalize(self, message_id: int, note: str) -> None:
        """Remove buttons and append the outcome so the thread shows history."""
        await self.call("editMessageReplyMarkup", chat_id=self.chat_id,
                        message_id=message_id, reply_markup={"inline_keyboard": []})
        await self.send(note, reply_to_message_id=message_id)

    # -- inbound -----------------------------------------------------------
    async def _poll(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = await self.call("getUpdates", offset=offset, timeout=30,
                                          allowed_updates=["message", "callback_query"]) or []
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network blip — keep polling
                log.warning("poll error: %s", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    await self._on_update(u)
                except Exception:
                    log.exception("update handling failed")

    def _candidate(self, u: dict) -> tuple[str, int, Answer] | None:
        """What this update *claims* to answer, or None.

        Pure and side-effect free. It deliberately does not decide whether the
        request is still open — that is the caller's single guard, so there is
        exactly one place where an update is accepted.
        """
        if cq := u.get("callback_query"):
            msg = cq.get("message") or {}
            if msg.get("chat", {}).get("id") != self.chat_id:
                return None
            rid, _, val = (cq.get("data") or "").partition(":")
            return rid, msg.get("message_id"), ("button", val)

        if m := u.get("message"):
            if m.get("chat", {}).get("id") != self.chat_id:
                return None
            reply = m.get("reply_to_message") or {}
            rid = self.msg_to_rid.get(reply.get("message_id"))
            text = (m.get("text") or "").strip()
            if rid and text:
                return rid, reply["message_id"], ("text", text)
        return None

    async def _on_update(self, u: dict) -> None:
        """The only inbound path. Answer an open request, or drop and record.

        There is no other branch by design — no commands, no queries, nothing
        the chat can initiate. See the invariant at the top of this module.
        """
        log.debug("telegram <- update %s", u.get("update_id"))
        cand = self._candidate(u)
        if cand is None or cand[0] not in self.pending:
            await self._drop(u)
            return

        rid, message_id, answer = cand
        if not self._resolve(rid, answer):      # raced a timeout between check and set
            await self._drop(u)
            return

        if cq := u.get("callback_query"):
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
            keyboard = (cq.get("message") or {}).get("reply_markup", {}).get(
                "inline_keyboard", [])
            label = next((b["text"] for row in keyboard for b in row
                          if b.get("callback_data") == cq.get("data")), answer[1])
        else:
            label = answer[1][:200]
        await self._finalize(message_id, f"✔ {html.escape(label)}")

    # Telegram narrates our own actions back to us as service messages — a
    # created topic, a changed title. They carry no text and nobody sent them,
    # so recording them as "someone tried to initiate" would bury the real
    # signal under one entry per topic we open.
    SERVICE_KEYS = frozenset({
        "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo",
        "delete_chat_photo", "pinned_message", "message_auto_delete_timer_changed",
        "group_chat_created", "supergroup_chat_created", "channel_chat_created",
        "migrate_to_chat_id", "migrate_from_chat_id",
    })

    @classmethod
    def _is_service(cls, m: dict) -> bool:
        return any(k.startswith("forum_topic_") or k.startswith("general_forum_topic_")
                   or k in cls.SERVICE_KEYS for k in m)

    async def _drop(self, u: dict) -> None:
        """Nothing was open for this. Record it — it is a signal, not noise.

        Updates from another chat are ignored without a trace and without a
        reply: they are not evidence about *our* chat, and answering one would
        confirm the bot is alive to whoever sent it.
        """
        if cq := u.get("callback_query"):
            if (cq.get("message") or {}).get("chat", {}).get("id") != self.chat_id:
                return
            kind, text = "callback", cq.get("data", "")
            # Without this the phone shows a spinner until it times out.
            await self.call("answerCallbackQuery", callback_query_id=cq["id"],
                            text="This request already expired")
        else:
            m = u.get("message") or {}
            if m.get("chat", {}).get("id") != self.chat_id:
                return
            if self._is_service(m):
                log.debug("ignoring telegram service message")
                return
            kind, text = "message", (m.get("text") or "")
        log.warning("dropped unsolicited %s: %r", kind, text[:120])
        STORE.reject(kind, text)
        AUDIT.write("rejected_unsolicited", chat_id=self.chat_id, kind=kind, text=text[:300])


# --------------------------------------------------------------------------
# Hook handlers
# --------------------------------------------------------------------------
def esc(s: Any) -> str:
    return html.escape(str(s))


def session_tag(ev: dict) -> str:
    """Inline identity line — redundant inside a per-session topic, kept for flat mode."""
    if SCOPE == "session":
        return ""
    cwd = os.path.basename((ev.get("cwd") or "").rstrip("/")) or "?"
    sid = (ev.get("session_id") or "")[:8]
    # A notification may carry no session id; an empty <code></code> looks broken.
    return f"📁 {esc(cwd)}" + (f" · <code>{esc(sid)}</code>\n" if sid else "\n")


def summarize_tool(tool: str, inp: dict) -> str:
    """One line describing a tool call, with secrets already removed.

    Every path that turns `tool_input` into text for the chat or the admin page
    goes through here, so this is the one place redaction has to happen. It
    returns the text only; `summarize_counted` gives the number hidden.
    """
    return summarize_counted(tool, inp)[0]


def summarize_counted(tool: str, inp: dict) -> tuple[str, int]:
    if tool == "Bash":
        raw = inp.get("command", "")
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        raw = inp.get("file_path", "")   # never the content
    else:
        raw = json.dumps(inp, ensure_ascii=False)
    text, hidden = REDACTOR.scrub(raw)
    # Truncate after redacting, so a secret cannot survive by straddling the cut.
    return (text if len(text) < 600 else text[:600] + "…"), hidden


def describe(ev: dict) -> str:
    """One line for the admin feed: what this event is actually about."""
    name = ev.get("hook_event_name")
    if name == "Stop":
        return REDACTOR.scrub((ev.get("last_assistant_message") or "").strip())[0][:300]
    if name == "Notification":
        return REDACTOR.scrub(ev.get("message", ""))[0]
    if name == "SessionEnd":
        return ev.get("reason", "")
    inp = ev.get("tool_input", {}) or {}
    if ev.get("tool_name") == "AskUserQuestion":
        return " · ".join(q.get("question", "") for q in inp.get("questions", []))[:300]
    return summarize_tool(ev.get("tool_name", ""), inp)


def outcome_of(name: str | None, out: dict, hint: str | None = None) -> str:
    """How the hook was answered, as shown in the admin feed."""
    spec = out.get("hookSpecificOutput") or {}
    if name == "PermissionRequest":
        decision = spec.get("decision") or {}
        behavior = decision.get("behavior")
        if behavior == "deny" and decision.get("message"):
            return "deny (reason)"
        return behavior or hint or "terminal"
    if name == "PreToolUse":
        answers = (spec.get("updatedInput") or {}).get("answers")
        return f"answered ({len(answers)})" if answers else (hint or "terminal")
    if name in ("Stop", "Notification", "SessionEnd"):
        return "sent"
    return "passthrough"


async def on_permission(chan: Channel, ev: dict, thread: Any) -> dict:
    tool = ev.get("tool_name", "?")
    if tool == "AskUserQuestion":  # handled on PreToolUse; let the CLI proceed
        return {}
    summary, hidden = summarize_counted(tool, ev.get("tool_input", {}) or {})
    text = (f"🔐 <b>{esc(tool)}</b> wants permission\n{session_tag(ev)}"
            f"<pre>{esc(summary)}</pre>{redact.note(hidden)}")
    ans = await chan.ask(text, [("✅ Allow", "allow"), ("❌ Deny", "deny"),
                                ("💻 Answer in terminal", "ask")], WAIT, thread,
                         hint="Tap a button, or reply <b>y</b> / <b>n</b>. "
                              "Any other reply denies, with your text as the reason.")
    if ans is None or ans == ("button", "ask"):
        store.outcome_hint.set("timeout" if ans is None else "terminal")
        store.answer_source.set("timeout" if ans is None else "terminal")
        return {}  # no decision → normal terminal prompt
    kind, val = ans
    store.answer_source.set(kind)
    if kind == "text":
        # A bare y/n is the decision; anything else denies and carries the text
        # to Claude as the reason. See replies.py for why allowing is strict.
        behavior, reason = replies.permission(val)
        decision: dict[str, Any] = {"behavior": behavior}
        if reason:
            decision["message"] = reason
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": decision}}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": {"behavior": val}}}


async def on_ask_user_question(chan: Channel, ev: dict, thread: Any) -> dict:
    inp = ev.get("tool_input", {}) or {}
    answers: dict[str, str] = {}
    remarks: list[str] = []
    for q in inp.get("questions", []):
        opts = q.get("options", []) or []
        lines = [f"❓ <b>{esc(q.get('header', 'Question'))}</b>",
                 esc(q.get("question", "")), session_tag(ev)]
        for i, o in enumerate(opts, 1):
            desc = f" — {esc(o.get('description', ''))}" if o.get("description") else ""
            lines.append(f"<b>{i}.</b> {esc(o.get('label', ''))}{desc}")
        buttons = [(f"{i}. {o.get('label', '')[:40]}", str(i)) for i, o in enumerate(opts, 1)]
        buttons.append(("💻 Answer in terminal", "ask"))
        ans = await chan.ask("\n".join(lines), buttons, WAIT, thread,
                             hint="Tap an option, or reply with its number — "
                                  "add a comment after a dash.")
        if ans is None or ans == ("button", "ask"):
            store.outcome_hint.set("timeout" if ans is None else "terminal")
            store.answer_source.set("timeout" if ans is None else "terminal")
            return {}  # fall back to the CLI's own picker
        kind, val = ans
        store.answer_source.set(kind)
        if kind == "button":
            val = opts[int(val) - 1].get("label", val)
        else:
            # "2", "2 - but check the migration first", or the label itself.
            labels = [o.get("label", "") for o in opts]
            picked, comment = replies.choice(val, labels)
            if picked:
                val = picked
                if comment:
                    # `answers` maps a question to *the selected option label*.
                    # A decorated value is not a label, so the remark travels in
                    # additionalContext, which exists to put text in front of
                    # Claude alongside the tool result.
                    remarks.append(f"On \"{q.get('question', '')}\" the user added: {comment}")
        answers[q.get("question", "")] = val
    out: dict[str, Any] = {"hookEventName": "PreToolUse",
                           "permissionDecision": "allow",
                           # Echo the original questions back and add `answers`;
                           # "allow" alone is not enough for AskUserQuestion.
                           "updatedInput": {**inp, "answers": answers}}
    if remarks:
        out["additionalContext"] = "\n".join(remarks)
    return {"hookSpecificOutput": out}


async def on_stop(chan: Channel, ev: dict, thread: Any) -> dict:
    msg, hidden = REDACTOR.scrub((ev.get("last_assistant_message") or "").strip())
    if msg:
        asyncio.create_task(chan.send(
            f"🏁 <b>Done</b>\n{session_tag(ev)}\n{esc(msg[:3000])}{redact.note(hidden)}",
            thread=thread))
    return {}


async def on_notification(chan: Channel, ev: dict, thread: Any) -> dict:
    asyncio.create_task(chan.send(
        f"⏳ {esc(ev.get('message', 'Claude is waiting for input'))}\n{session_tag(ev)}",
        thread=thread))
    return {}


async def on_session_end(chan: Channel, ev: dict, thread: Any) -> dict:
    asyncio.create_task(chan.close_thread(ev))
    return {}


def authorized(request: web.Request) -> bool:
    if not TOKEN:
        return True
    header = request.headers.get("Authorization", "")
    presented = header[7:] if header.startswith("Bearer ") else ""
    # compare_digest on equal-length encodings; both sides hashed so a wrong
    # length doesn't leak through the comparison
    return hmac.compare_digest(presented.encode(), TOKEN.encode())


async def hook_endpoint(request: web.Request) -> web.Response:
    if not authorized(request):
        log.warning("rejected hook from %s: bad or missing token", request.remote)
        AUDIT.write("auth_failure", source=request.remote or "?", path=request.path)
        return web.json_response({"error": "unauthorized"}, status=401)
    chan: Channel = request.app["chan"]
    ev = await request.json()
    name = ev.get("hook_event_name")
    log.info("hook %s tool=%s session=%s", name, ev.get("tool_name"),
             (ev.get("session_id") or "")[:8])
    store.current_event.set(ev)          # so Channel.ask can attribute prompts
    store.outcome_hint.set(None)
    store.answer_source.set(None)
    rec = STORE.record(ev, describe(ev))
    # The full input, not the redacted summary the chat sees: this is evidence.
    ref = AUDIT.write("request_open", session_id=ev.get("session_id", ""),
                      cwd=ev.get("cwd", ""), event=name or "?",
                      tool=ev.get("tool_name"), input=ev.get("tool_input") or {})
    try:
        if name == "SessionEnd":
            out = await on_session_end(chan, ev, None)
        else:
            thread = await chan.thread_for(ev)
            STORE.session(ev).topic_id = thread
            if name == "PermissionRequest":
                out = await on_permission(chan, ev, thread)
            elif name == "PreToolUse" and ev.get("tool_name") == "AskUserQuestion":
                out = await on_ask_user_question(chan, ev, thread)
            elif name == "Stop":
                out = await on_stop(chan, ev, thread)
            elif name == "Notification":
                out = await on_notification(chan, ev, thread)
            else:
                out = {}
    except Exception:
        log.exception("handler failed; falling back to terminal")
        out = {}
    outcome = outcome_of(name, out, store.outcome_hint.get())
    STORE.complete(rec, outcome)
    log.info("hook %s -> %s via %s in %sms", name, outcome,
             store.answer_source.get() or "-", rec.duration_ms)
    AUDIT.write("decision", ref=ref, session_id=ev.get("session_id", ""),
                outcome=outcome, source=store.answer_source.get(),
                latency_ms=rec.duration_ms)
    return web.json_response(out)


_notify_times: deque[float] = deque(maxlen=1000)


def _rate_ok() -> bool:
    """Fixed window over the last minute. A stuck loop must not flood the phone."""
    now = time.monotonic()
    while _notify_times and now - _notify_times[0] > 60:
        _notify_times.popleft()
    if len(_notify_times) >= NOTIFY_RATE:
        return False
    _notify_times.append(now)
    return True


async def notify_endpoint(request: web.Request) -> web.Response:
    """Agent-sent notification. Send-only, by design.

    This is the one message to the phone that does not originate from a hook.
    It still originates *on the Claude Code host*, so the security half of the
    invariant is untouched: there is no reply path here, nothing is awaited,
    and nothing from the chat can reach the caller. It calls `send`, never
    `ask` — and that is what keeps it a notification rather than a back door.
    """
    if not authorized(request):
        log.warning("rejected notify from %s: bad or missing token", request.remote)
        AUDIT.write("auth_failure", source=request.remote or "?", path=request.path)
        return web.json_response({"error": "unauthorized"}, status=401)
    if not NOTIFY:
        return web.json_response({"error": "notifications are disabled"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "body must be JSON"}, status=400)

    raw = str(body.get("message") or "").strip()
    if not raw:
        return web.json_response({"error": "message is required"}, status=400)
    if not _rate_ok():
        log.warning("notification dropped: over %d per minute", NOTIFY_RATE)
        return web.json_response({"error": f"rate limit: {NOTIFY_RATE}/min"}, status=429)

    text, hidden = REDACTOR.scrub(raw[:3000])
    cwd = body.get("cwd") or ""
    sid = body.get("session_id") or ""
    if not sid:
        # Claude Code does not tell an MCP server which session it serves, so
        # notify_user cannot send one. Infer it from the working directory,
        # which the server does know — otherwise every notification lands in
        # General rather than beside the prompts from the same session.
        sid = STORE.session_for_cwd(cwd) or ""
        if sid:
            log.debug("notification attributed to session %s by cwd %s", sid[:8], cwd)
    ev = {"session_id": sid, "cwd": cwd}
    chan: Channel = request.app["chan"]
    thread = await chan.thread_for(ev) if sid else None
    if thread is None and SCOPE != "flat":
        log.info("notification going to the general thread: no live session for cwd %r", cwd)
    title = {"warn": "⚠️", "error": "🔴"}.get(str(body.get("level", "info")), "🔔")
    await chan.send(f"{title} {session_tag(ev)}{esc(text)}{redact.note(hidden)}", thread=thread)

    AUDIT.write("notification", session_id=sid, cwd=cwd,
                level=body.get("level", "info"), message=raw, redacted=hidden)
    log.info("notification sent (%d chars, %d redacted)", len(text), hidden)
    return web.json_response({"ok": True, "redacted": hidden})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------
def preflight() -> ssl.SSLContext | None:
    """Refuse to expose an unauthenticated or cleartext listener on the network."""
    local = BIND in ("127.0.0.1", "::1", "localhost")
    problems = []
    if not local and not TOKEN:
        problems.append("BRIDGE_BIND is not loopback but BRIDGE_TOKEN is unset — "
                        "anyone who can reach this port could approve your tool calls")
    if not local and not (TLS_CERT and TLS_KEY):
        problems.append("BRIDGE_BIND is not loopback but no BRIDGE_TLS_CERT/KEY — "
                        "the bearer token would cross the network in cleartext")
    if TOKEN and len(TOKEN) < 32:
        problems.append("BRIDGE_TOKEN is shorter than 32 characters")
    if ADMIN and not local and not ADMIN_TOKEN:
        problems.append("BRIDGE_ADMIN=1 on a non-loopback bind with no "
                        "BRIDGE_ADMIN_TOKEN — the admin page shows tool inputs "
                        "and session history to anyone who can reach the port")
    if problems:
        for p in problems:
            log.error("%s", p)
        if not INSECURE:
            log.error("refusing to start. Run ./make-certs.sh, or set BRIDGE_INSECURE=1 "
                      "to override on a trusted lab network.")
            sys.exit(1)
        log.warning("BRIDGE_INSECURE=1 — starting anyway")

    if not (TLS_CERT and TLS_KEY):
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(TLS_CERT, TLS_KEY)
    return ctx


def startup_card(chan: Any, ssl_ctx: Any) -> str:
    """Posted to the General topic, so several bridges in one group are told apart.

    Identity only: which instance is this, which bot, which chat. Deliberately
    **no security posture** — a chat is the wrong place to publish which
    controls are off, and anyone reading it is either you or someone you would
    rather not hand a list of weaknesses. The full configuration fingerprint
    goes to the audit log's `bridge_start` record instead, which stays on the
    host.
    """
    rows = [
        ("host", socket.gethostname()),
        ("version", __version__),
        ("bot", f"@{getattr(chan, 'username', '?')}"),
        ("chat", f"{TG_CHAT_ID}" + (" · forum" if getattr(chan, "is_forum", False) else "")),
        ("scope", f"{SCOPE} · answer within {WAIT}s"),
    ]
    body = "\n".join(f"{k:<9}{esc(v)}" for k, v in rows)
    return f"🟢 <b>bridge online</b>\n<pre>{body}</pre>"


def _shutdown(sig: signal.Signals, stop: asyncio.Event) -> None:
    log.info("%s received — shutting down", sig.name)
    stop.set()


async def main() -> None:
    access_log = logs.setup(LOG_LEVEL, LOG_FILE, LOG_ACCESS, REDACTOR)
    log.debug("configuration: bind=%s port=%s scope=%s timeout=%s admin=%s audit=%s",
              BIND, PORT, SCOPE, WAIT, ADMIN, AUDIT.path)
    ssl_ctx = preflight()
    AUDIT.write("bridge_start", version=__version__, config={
        "bind": BIND, "port": PORT, "scope": SCOPE, "timeout": WAIT,
        "tls": bool(TLS_CERT and TLS_KEY), "hook_auth": bool(TOKEN),
        "admin": ADMIN, "admin_auth": bool(ADMIN_TOKEN),
        "redact": REDACTOR.enabled, "audit_chained": bool(AUDIT.key),
    })
    if TG_API_BASE != "https://api.telegram.org":
        log.warning("using non-default Bot API base %s", TG_API_BASE)
    chan = TelegramChannel(TG_BOT_TOKEN, TG_CHAT_ID)
    await chan.start()
    app = web.Application()
    app["chan"] = chan
    app.router.add_post("/hook", hook_endpoint)
    app.router.add_post("/notify", notify_endpoint)
    app.router.add_get("/health", health)
    if ADMIN:
        admin.attach(app, STORE, ADMIN_TOKEN)
    runner = web.AppRunner(app, access_log=access_log)
    await runner.setup()
    await web.TCPSite(runner, BIND, PORT, ssl_context=ssl_ctx).start()
    scheme = "https" if ssl_ctx else "http"
    log.info("listening on %s://%s:%d/hook (auth: %s)",
             scheme, BIND, PORT, "on" if TOKEN else "OFF")
    if ADMIN:
        log.info("admin page on %s://%s:%d/admin (auth: %s, history in memory only)",
                 scheme, BIND, PORT, "on" if ADMIN_TOKEN else "OFF")
    await chan.send(startup_card(chan, ssl_ctx))

    # Without this, SIGTERM — what kill and systemd send — terminates the
    # process where it stands and the shutdown below never runs. Only Ctrl-C
    # would have unwound it, which is not how anything stops a daemon.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, _shutdown, sig, stop)

    try:
        await stop.wait()
    finally:
        # Best effort: Telegram may be unreachable, and a SIGKILL or an OOM
        # kill never reaches here at all. A missing offline note means the
        # bridge died badly, which is worth knowing in itself.
        with contextlib.suppress(Exception):
            await chan.send(f"🔴 <b>bridge offline</b> · "
                            f"<code>{esc(socket.gethostname())}</code>")
        AUDIT.write("bridge_stop")
        AUDIT.close()
        await chan.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
