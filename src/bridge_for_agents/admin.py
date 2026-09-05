"""Read-only web admin page: which sessions are live, and what happened.

Served by the same aiohttp app as the hook endpoint, under /admin. It shows
what the bridge is holding in memory (see :mod:`bridge_for_agents.store`) and
has no way to answer a prompt or change state — decisions stay on the phone.

Auth mirrors the hook endpoint: a bearer token, accepted either as an
``Authorization`` header or as ``?token=`` once, which sets a cookie so the
browser can keep navigating. A loopback bind with no token configured is left
open, exactly like the hook endpoint.
"""
from __future__ import annotations

import hmac
import json
from typing import Any

from aiohttp import web

COOKIE = "bridge_admin"

# Typed app keys (aiohttp 3.9+) — plain string keys are deprecated.
STORE_KEY: web.AppKey[Any] = web.AppKey("store")
TOKEN_KEY: web.AppKey[str] = web.AppKey("admin_token")


def _authorized(request: web.Request) -> bool:
    token = request.app[TOKEN_KEY]
    if not token:
        return True
    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[7:]
    elif (q := request.query.get("token")) is not None:
        presented = q
    elif (c := request.cookies.get(COOKIE)) is not None:
        presented = c
    return hmac.compare_digest(presented.encode(), token.encode())


def _deny() -> web.Response:
    return web.Response(status=401, content_type="text/html", text=UNAUTHORIZED_HTML)


async def page(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _deny()
    resp = web.Response(content_type="text/html", text=PAGE_HTML)
    # Remember a token that arrived in the query string so the page can poll,
    # and so the secret stops travelling in the URL bar.
    if (q := request.query.get("token")) and request.app[TOKEN_KEY]:
        resp.set_cookie(COOKIE, q, httponly=True, samesite="Strict",
                        secure=request.scheme == "https", max_age=86400)
    return resp


async def state(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    store = request.app[STORE_KEY]
    snap: dict[str, Any] = store.snapshot(request.query.get("session"))
    return web.json_response(snap, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def attach(app: web.Application, store: Any, admin_token: str) -> None:
    app[STORE_KEY] = store
    app[TOKEN_KEY] = admin_token
    app.router.add_get("/admin", page)
    app.router.add_get("/admin/api/state", state)


UNAUTHORIZED_HTML = """<!doctype html><meta charset=utf-8>
<title>bridge-for-agents · unauthorized</title>
<style>
 body{font:15px/1.6 system-ui,sans-serif;background:#0f1115;color:#e6e6e6;
      display:grid;place-items:center;height:100vh;margin:0}
 div{max-width:34rem;padding:2rem}
 code{background:#1b1f27;padding:.15em .4em;border-radius:4px;color:#8ab4f8}
</style>
<div>
 <h1>401 · unauthorized</h1>
 <p>Append your admin token to the URL once:</p>
 <p><code>/admin?token=&lt;BRIDGE_ADMIN_TOKEN&gt;</code></p>
 <p>It is then stored in an HttpOnly cookie for a day.</p>
</div>
"""

PAGE_HTML = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>bridge-for-agents · admin</title>
<style>
 :root{
   --bg:#0f1115; --panel:#161a21; --panel2:#1b2029; --line:#262c37;
   --fg:#e6e8ec; --dim:#8b93a1; --accent:#6ea8fe; --ok:#4ade80;
   --warn:#fbbf24; --bad:#f87171; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
 }
 @media (prefers-color-scheme:light){
   :root{--bg:#f6f7f9;--panel:#fff;--panel2:#f0f2f5;--line:#dde1e7;
         --fg:#1a1d23;--dim:#666e7b;--accent:#1a56db}
 }
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
 header{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
        padding:.7rem 1rem;border-bottom:1px solid var(--line);background:var(--panel)}
 h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.2px}
 h1 span{color:var(--dim);font-weight:400}
 .stats{display:flex;gap:1.1rem;margin-left:auto;flex-wrap:wrap}
 .stat b{font-variant-numeric:tabular-nums;font-size:15px}
 .stat i{color:var(--dim);font-style:normal;font-size:12px;margin-left:.3em}
 .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);display:inline-block;
      margin-right:.4em;vertical-align:1px}
 .dot.stale{background:var(--bad)}
 main{display:grid;grid-template-columns:minmax(240px,20rem) 1fr;
      height:calc(100vh - 49px)}
 @media (max-width:820px){main{grid-template-columns:1fr;height:auto}}
 aside{border-right:1px solid var(--line);overflow-y:auto;background:var(--panel)}
 section{overflow-y:auto;padding:1rem}
 .sess{padding:.6rem .9rem;border-bottom:1px solid var(--line);cursor:pointer}
 .sess:hover{background:var(--panel2)}
 .sess.on{background:var(--panel2);box-shadow:inset 3px 0 0 var(--accent)}
 .sess .top{display:flex;justify-content:space-between;gap:.5rem;align-items:baseline}
 .sess .name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .sess .meta{color:var(--dim);font-size:12px;display:flex;justify-content:space-between;
             gap:.5rem;margin-top:.15rem}
 .sid{font-family:var(--mono);font-size:11px;color:var(--dim)}
 .badge{font-size:11px;padding:.05em .45em;border-radius:99px;border:1px solid var(--line);
        color:var(--dim);white-space:nowrap}
 .badge.live{color:var(--ok);border-color:var(--ok)}
 .badge.ended{color:var(--dim)}
 .waiting{border:1px solid var(--warn);border-radius:8px;padding:.8rem 1rem;
          margin-bottom:1rem;background:color-mix(in srgb,var(--warn) 8%,transparent)}
 .waiting h2{margin:0 0 .5rem;font-size:13px;color:var(--warn);
             text-transform:uppercase;letter-spacing:.08em}
 .waiting pre{margin:.3rem 0;white-space:pre-wrap;font-family:var(--mono);font-size:12.5px}
 .waiting .opts{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
 .waiting .opts span{border:1px solid var(--line);border-radius:5px;
                     padding:.1em .5em;font-size:12px;color:var(--dim)}
 .countdown{font-variant-numeric:tabular-nums;color:var(--warn);font-weight:600}
 h2.title{font-size:13px;text-transform:uppercase;letter-spacing:.08em;
          color:var(--dim);margin:0 0 .6rem}
 table{width:100%;border-collapse:collapse}
 th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
    color:var(--dim);font-weight:500;padding:.3rem .5rem;border-bottom:1px solid var(--line)}
 td{padding:.4rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
 tr:hover td{background:var(--panel2)}
 td.t{white-space:nowrap;color:var(--dim);font-family:var(--mono);font-size:12px}
 td.sum{font-family:var(--mono);font-size:12.5px;word-break:break-word;max-width:0;width:99%}
 .ev{font-size:11.5px;padding:.1em .5em;border-radius:4px;border:1px solid var(--line);
     white-space:nowrap}
 .ev.PermissionRequest{color:var(--accent);border-color:var(--accent)}
 .ev.PreToolUse{color:#c084fc;border-color:#c084fc}
 .ev.SessionEnd{color:var(--dim)}
 .out{font-size:11.5px;white-space:nowrap}
 .out.allow{color:var(--ok)} .out.deny{color:var(--bad)}
 .out.terminal,.out.timeout{color:var(--warn)} .out.pending{color:var(--dim)}
 .empty{color:var(--dim);padding:2rem 0;text-align:center}
 footer{color:var(--dim);font-size:12px;padding:.6rem 1rem;border-top:1px solid var(--line)}
 button{font:inherit;background:var(--panel2);color:var(--fg);border:1px solid var(--line);
        border-radius:6px;padding:.2rem .6rem;cursor:pointer}
</style>

<header>
  <h1><span id=live class=dot></span>bridge-for-agents <span>· admin</span></h1>
  <div class=stats>
    <div class=stat><b id=s-active>–</b><i>live</i></div>
    <div class=stat><b id=s-sessions>–</b><i>sessions</i></div>
    <div class=stat><b id=s-events>–</b><i>events</i></div>
    <div class=stat><b id=s-waiting>–</b><i>waiting</i></div>
    <div class=stat><b id=s-uptime>–</b><i>uptime</i></div>
    <button id=pause>pause</button>
  </div>
</header>

<main>
  <aside id=sessions></aside>
  <section>
    <div id=waiting></div>
    <h2 class=title id=feed-title>Recent activity</h2>
    <div id=feed></div>
  </section>
</main>

<footer>
  History is in memory only — it starts over when the bridge restarts.
  This page is read&#8209;only; answer prompts from the chat.
</footer>

<script>
const $ = s => document.querySelector(s);
let selected = null, paused = false, timer = null;

const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString();
const fmtAgo = s => s < 60 ? Math.round(s) + "s"
  : s < 3600 ? Math.round(s / 60) + "m"
  : s < 86400 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d";
const esc = s => String(s ?? "").replace(/[<>&"]/g,
  c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

function outcomeClass(o) {
  if (!o) return "pending";
  if (o.startsWith("allow")) return "allow";
  if (o.startsWith("deny")) return "deny";
  if (o === "timeout" || o === "terminal") return o;
  return "pending";
}

function renderSessions(d) {
  if (!d.sessions.length) {
    $("#sessions").innerHTML = '<p class=empty>No sessions yet.</p>';
    return;
  }
  $("#sessions").innerHTML = d.sessions.map(s => `
    <div class="sess ${s.session_id === selected ? "on" : ""}" data-id="${esc(s.session_id)}">
      <div class=top>
        <span class=name title="${esc(s.cwd)}">${esc(s.project)}</span>
        <span class="badge ${s.ended ? "ended" : "live"}">${s.ended ? "ended" : "live"}</span>
      </div>
      <div class=meta>
        <span class=sid>${esc(s.short_id)}</span>
        <span>${s.total} ev · ${fmtAgo(d.now - s.last_seen)} ago</span>
      </div>
    </div>`).join("");
}

function renderWaiting(d) {
  const w = d.waiting.filter(p => !selected || p.session_id === selected);
  $("#waiting").innerHTML = w.map(p => `
    <div class=waiting>
      <h2>Waiting for an answer · ${p.remaining}s left</h2>
      <pre>${esc(p.text)}</pre>
      <div class=opts>${p.options.map(o => `<span>${esc(o)}</span>`).join("")}</div>
    </div>`).join("");
}

function renderFeed(d) {
  const rows = selected && d.selected ? d.selected.events : d.feed;
  $("#feed-title").textContent = selected
    ? `Session ${d.selected ? d.selected.short_id : ""} · ${rows.length} events`
    : "Recent activity · all sessions";
  if (!rows.length) {
    $("#feed").innerHTML = '<p class=empty>Nothing recorded yet. '
      + 'Trigger a permission prompt in Claude Code.</p>';
    return;
  }
  $("#feed").innerHTML = `<table><thead><tr>
      <th>Time</th><th>Event</th><th>Tool</th><th>Detail</th>
      <th>Outcome</th><th>Took</th>${selected ? "" : "<th>Session</th>"}
    </tr></thead><tbody>` + rows.map(e => `
    <tr>
      <td class=t>${fmtTime(e.ts)}</td>
      <td><span class="ev ${esc(e.event)}">${esc(e.event)}</span></td>
      <td class=t>${esc(e.tool || "")}</td>
      <td class=sum>${esc(e.summary || "")}</td>
      <td class="out ${outcomeClass(e.outcome)}">${esc(e.outcome || "…")}</td>
      <td class=t>${e.duration_ms != null ? (e.duration_ms / 1000).toFixed(1) + "s" : ""}</td>
      ${selected ? "" : `<td class=sid>${esc(e.session_id.slice(0, 8))}</td>`}
    </tr>`).join("") + "</tbody></table>";
}

async function tick() {
  try {
    const url = "/admin/api/state" + (selected ? "?session=" + encodeURIComponent(selected) : "");
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.reload(); return; }
    const d = await r.json();
    $("#live").classList.remove("stale");
    $("#s-active").textContent = d.totals.active;
    $("#s-sessions").textContent = d.totals.sessions;
    $("#s-events").textContent = d.totals.events;
    $("#s-waiting").textContent = d.totals.waiting;
    $("#s-uptime").textContent = fmtAgo(d.uptime);
    document.title = (d.totals.waiting ? `(${d.totals.waiting}) ` : "")
      + "bridge-for-agents · admin";
    renderSessions(d); renderWaiting(d); renderFeed(d);
  } catch (e) {
    $("#live").classList.add("stale");
  }
}

$("#sessions").addEventListener("click", ev => {
  const el = ev.target.closest(".sess");
  if (!el) return;
  selected = el.dataset.id === selected ? null : el.dataset.id;
  tick();
});

$("#pause").addEventListener("click", () => {
  paused = !paused;
  $("#pause").textContent = paused ? "resume" : "pause";
  clearInterval(timer);
  if (!paused) { timer = setInterval(tick, 2000); tick(); }
});

timer = setInterval(tick, 2000);
tick();
</script>
"""
