#!/usr/bin/env python3
"""
claude-bridge — MVP
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
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import os
import ssl
import sys
import uuid
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from . import admin, store

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
ADMIN = os.environ.get("BRIDGE_ADMIN") == "1"
ADMIN_TOKEN = os.environ.get("BRIDGE_ADMIN_TOKEN", "") or TOKEN
HISTORY = int(os.environ.get("BRIDGE_ADMIN_HISTORY", "200"))

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
                  thread: Any = None) -> Answer | None:
        """Show `text` with `options` [(label, value)], wait for a button press
        or a free-text reply. Returns None on timeout."""
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
        self.http: ClientSession | None = None
        self._poll_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        # trust_env: pick up HTTPS_PROXY / NO_PROXY from the environment
        self.http = ClientSession(timeout=ClientTimeout(total=45), trust_env=True)
        await self.call("deleteWebhook")  # long polling needs no webhook
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
        async with self.http.post(f"{self.api}/{method}", json=params) as r:
            data = await r.json()
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, data.get("description"))
            return None
        return data.get("result")

    async def send(self, text: str, thread: Any = None, **kw: Any) -> Any:
        return await self.call(
            "sendMessage", chat_id=self.chat_id, message_thread_id=thread,
            text=text[:4000], parse_mode="HTML", disable_web_page_preview=True, **kw,
        )

    async def ask(self, text: str, options: list[tuple[str, str]], timeout: int,
                  thread: Any = None) -> Answer | None:
        rid = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        STORE.open_prompt(rid, text, [lbl for lbl, _ in options], timeout)
        keyboard = {"inline_keyboard": [[{"text": lbl, "callback_data": f"{rid}:{val}"}]
                                        for lbl, val in options]}
        msg = await self.send(text + "\n\n<i>Tap a button or reply to this message.</i>",
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

    async def _on_update(self, u: dict) -> None:
        if cq := u.get("callback_query"):
            msg = cq.get("message") or {}
            if msg.get("chat", {}).get("id") != self.chat_id:
                return
            rid, _, val = cq.get("data", "").partition(":")
            keyboard = msg.get("reply_markup", {}).get("inline_keyboard", [])
            label = next((b["text"] for row in keyboard
                          for b in row if b.get("callback_data") == cq.get("data")), val)
            ok = self._resolve(rid, ("button", val))
            await self.call("answerCallbackQuery", callback_query_id=cq["id"],
                            text=None if ok else "This request already expired")
            if ok:
                await self._finalize(msg["message_id"], f"✔ {html.escape(label)}")
        elif m := u.get("message"):
            if m.get("chat", {}).get("id") != self.chat_id:
                return
            reply = m.get("reply_to_message")
            rid = reply and self.msg_to_rid.get(reply["message_id"])
            thread = m.get("message_thread_id")
            text = (m.get("text") or "").strip()
            if rid and text:
                if self._resolve(rid, ("text", text)):
                    await self._finalize(reply["message_id"], f"✔ {html.escape(text[:200])}")
            elif text == "/ping":
                await self.send("pong 🟢", thread=thread)
            elif text == "/pending":
                await self.send(f"{len(self.pending)} pending request(s) · "
                                f"{len(self.topics)} open topic(s)", thread=thread)


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
    return f"📁 {esc(cwd)} · <code>{esc(ev.get('session_id', '')[:8])}</code>\n"


def summarize_tool(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return inp.get("command", "")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        return inp.get("file_path", "")
    s = json.dumps(inp, ensure_ascii=False)
    return s if len(s) < 600 else s[:600] + "…"


def describe(ev: dict) -> str:
    """One line for the admin feed: what this event is actually about."""
    name = ev.get("hook_event_name")
    if name == "Stop":
        return (ev.get("last_assistant_message") or "").strip()[:300]
    if name == "Notification":
        return ev.get("message", "")
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
    text = (f"🔐 <b>{esc(tool)}</b> wants permission\n{session_tag(ev)}"
            f"<pre>{esc(summarize_tool(tool, ev.get('tool_input', {})))}</pre>")
    ans = await chan.ask(text, [("✅ Allow", "allow"), ("❌ Deny", "deny"),
                                ("💻 Answer in terminal", "ask")], WAIT, thread)
    if ans is None or ans[1] == "ask":
        store.outcome_hint.set("timeout" if ans is None else "terminal")
        return {}  # no decision → normal terminal prompt
    kind, val = ans
    if kind == "text":  # free text on a permission = deny with a reason for Claude
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "deny", "message": val}}}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": {"behavior": val}}}


async def on_ask_user_question(chan: Channel, ev: dict, thread: Any) -> dict:
    inp = ev.get("tool_input", {}) or {}
    answers: dict[str, str] = {}
    for q in inp.get("questions", []):
        opts = q.get("options", []) or []
        lines = [f"❓ <b>{esc(q.get('header', 'Question'))}</b>",
                 esc(q.get("question", "")), session_tag(ev)]
        for i, o in enumerate(opts, 1):
            desc = f" — {esc(o.get('description', ''))}" if o.get("description") else ""
            lines.append(f"<b>{i}.</b> {esc(o.get('label', ''))}{desc}")
        buttons = [(f"{i}. {o.get('label', '')[:40]}", str(i)) for i, o in enumerate(opts, 1)]
        buttons.append(("💻 Answer in terminal", "ask"))
        ans = await chan.ask("\n".join(lines), buttons, WAIT, thread)
        if ans is None or ans[1] == "ask":
            store.outcome_hint.set("timeout" if ans is None else "terminal")
            return {}  # fall back to the CLI's own picker
        kind, val = ans
        if kind == "button":
            val = opts[int(val) - 1].get("label", val)
        answers[q.get("question", "")] = val
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "allow",
                                   "updatedInput": {**inp, "answers": answers}}}


async def on_stop(chan: Channel, ev: dict, thread: Any) -> dict:
    msg = (ev.get("last_assistant_message") or "").strip()
    if msg:
        asyncio.create_task(chan.send(f"🏁 <b>Done</b>\n{session_tag(ev)}\n{esc(msg[:3000])}",
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
        return web.json_response({"error": "unauthorized"}, status=401)
    chan: Channel = request.app["chan"]
    ev = await request.json()
    name = ev.get("hook_event_name")
    log.info("hook %s tool=%s session=%s", name, ev.get("tool_name"),
             (ev.get("session_id") or "")[:8])
    store.current_event.set(ev)          # so Channel.ask can attribute prompts
    store.outcome_hint.set(None)
    rec = STORE.record(ev, describe(ev))
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
    STORE.complete(rec, outcome_of(name, out, store.outcome_hint.get()))
    return web.json_response(out)


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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ssl_ctx = preflight()
    if TG_API_BASE != "https://api.telegram.org":
        log.warning("using non-default Bot API base %s", TG_API_BASE)
    chan = TelegramChannel(TG_BOT_TOKEN, TG_CHAT_ID)
    await chan.start()
    app = web.Application()
    app["chan"] = chan
    app.router.add_post("/hook", hook_endpoint)
    app.router.add_get("/health", health)
    if ADMIN:
        admin.attach(app, STORE, ADMIN_TOKEN)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, BIND, PORT, ssl_context=ssl_ctx).start()
    scheme = "https" if ssl_ctx else "http"
    log.info("listening on %s://%s:%d/hook (auth: %s)",
             scheme, BIND, PORT, "on" if TOKEN else "OFF")
    if ADMIN:
        log.info("admin page on %s://%s:%d/admin (auth: %s, history in memory only)",
                 scheme, BIND, PORT, "on" if ADMIN_TOKEN else "OFF")
    await chan.send("🟢 claude-bridge online")
    try:
        await asyncio.Event().wait()
    finally:
        await chan.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
