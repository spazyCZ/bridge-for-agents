# bridge-for-agents

[![CI](https://github.com/spazyCZ/bridge-for-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/spazyCZ/bridge-for-agents/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Answer Claude Code's open questions and permission prompts from Telegram.
No Remote Control needed — built purely on Claude Code's HTTP hooks.

```
Claude Code ──HTTP hook──▶ bridge ──Bot API──▶ Telegram
Claude Code ◀──JSON decision── bridge ◀──button/reply── you
```

## Setup (5 minutes)

1. **Bot**: talk to `@BotFather` → `/newbot` → copy the token.
2. **Chat id**: send any message to your new bot, then
   `curl https://api.telegram.org/bot<TOKEN>/getUpdates` → `message.chat.id`.
3. **Run the bridge** (Python 3.11+):
   ```bash
   pip install .                       # or: pip install -e ".[dev]" to hack on it
   export TG_BOT_TOKEN=123:abc  TG_CHAT_ID=-1001234567890
   bridge-for-agents                   # or: python -m bridge_for_agents
   ```
   You should get "🟢 claude-bridge online" on Telegram. `/ping` answers `pong`.
4. **Hook it up**: merge `examples/hooks.settings.json` into `~/.claude/settings.json`
   (or a project's `.claude/settings.json`; with `CLAUDE_CONFIG_DIR` isolation
   put it in that config dir's `settings.json`). Check with `/hooks` in Claude Code.

## What you get on the phone

| Event | Message | Buttons |
|---|---|---|
| Permission prompt (any tool) | tool + command/file | Allow / Deny / Answer in terminal |
| `AskUserQuestion` | header, question, numbered options | one per option + terminal |
| Idle prompt (60 s waiting) | "waiting for input" | – |
| Turn finished | last assistant message | – |
| Session ended | closes the topic | – |

Everything above is also visible in the [web admin page](#web-admin-page).

- **Free text**: reply to the bot message. On a question it becomes the answer;
  on a permission it's a *deny with your text as the reason* (Claude reads it).
- **Fallback**: no answer within `BRIDGE_TIMEOUT` (540 s) or "Answer in terminal"
  → bridge returns `{}` → Claude Code shows its normal prompt in the terminal.
  Hook timeout in settings is 600 s so the bridge always answers first.
- **Multiple sessions**: each gets its own topic — see below.

## Session separation

Each session gets its own **forum topic** in a Telegram supergroup: separate
scrollback, separate unread badge, and replies land in the right thread.

Setup:

1. Create a group → convert to supergroup → **enable Topics** in group settings.
2. Add the bot as an admin with *Manage Topics*.
3. Chat id is negative for supergroups, e.g. `TG_CHAT_ID=-1001234567890`
   (from `getUpdates` after posting in the group).

Scope is chosen with `BRIDGE_SCOPE`:

| Value | One topic per | Topic name | Good for |
|---|---|---|---|
| `session` (default) | Claude Code session | `myrepo · a1b2c3d4` | many parallel sessions, clean separation |
| `project` | working directory | `myrepo` | few long-lived repos, less topic churn |
| `flat` | – | – | one chat, sessions tagged inline |

- The map from scope key → `message_thread_id` is persisted in `BRIDGE_STATE`
  (default `~/.local/state/claude-bridge/topics.json`), so a bridge restart
  reuses existing topics instead of creating duplicates.
- On `SessionEnd` the bridge posts "session ended" and **closes** the topic —
  it stays readable in the archive but drops out of the active list.
- If the chat isn't a forum, the bridge logs a warning and runs flat, with the
  `📁 cwd · session-id` tag on every message. Nothing breaks.

Note that separation is only cosmetic on top of correlation that already works:
every request carries a random id embedded in its buttons, so an answer resolves
exactly the request it belongs to even in flat mode. Topics are about *your*
ability to tell sessions apart.

### One poller per bot token

`getUpdates` is exclusive — two bridge instances on the same token fight over
updates (409 Conflict). The constraint is per **token**, not per group:
several bots can post into the same group quite happily. And you never need a
bot per session — that is what topics are for; one bridge serves many parallel
sessions.

Copying one token to two machines is the one configuration that misbehaves
silently, with each poller randomly stealing the other's updates.

### Deployment topologies

| | Bots | Bridges | Needs a token + TLS? |
|---|---|---|---|
| **One machine** | 1 | 1, on `127.0.0.1` | no |
| **Many machines, one bridge** | 1 | 1, on the LAN | **yes**, both |
| **Many machines, one bridge each** | 1 per machine | 1 per machine, loopback | no |

- **One machine** is the default and needs no security setup at all: a
  loopback bridge, unlimited sessions, one topic each.
- **One shared bridge** is the "run one bridge for all your hosts" case. The
  other hosts POST across the network, so `BRIDGE_TOKEN` and TLS are both
  mandatory — the preflight refuses to start without them.
- **A bridge per machine** keeps every listener on loopback, so there is no
  shared secret, no certificate and no exposed port anywhere. Each needs its
  own bot token, because of the exclusivity above, but they can all post into
  one group. Usually the easiest to secure; the cost is a `/newbot` per
  machine.

## Web admin page

A read-only dashboard at `/admin` on the same listener as the hook endpoint:
what Claude Code is waiting for **right now**, and what it has asked for
recently.

```bash
export BRIDGE_ADMIN=1
export BRIDGE_ADMIN_TOKEN=$(openssl rand -hex 32)   # required off loopback
bridge-for-agents
# open http://127.0.0.1:8765/admin?token=<BRIDGE_ADMIN_TOKEN>
```

- **Waiting now** — every outstanding prompt with its options and a countdown
  to `BRIDGE_TIMEOUT`, so you can see what is blocking a session before you
  reach for your phone.
- **Sessions** — one row per session or project: working directory, short
  session id, event counts, live/ended, last activity. Click one to filter.
- **Activity** — the event timeline: time, hook event, tool, the command or
  file path, the outcome (`allow`, `deny`, `deny (reason)`, `answered`,
  `terminal`, `timeout`) and how long the answer took.

The page polls `/admin/api/state` every two seconds; the same JSON is there if
you would rather script against it.

**It is read-only by design.** There is no way to approve or deny from the
browser — decisions stay in the chat, where the approval path is already
authenticated. The page only reports.

### History is in memory only

Sessions and events live in the daemon's process and are never written to
disk, so **restarting the bridge starts the history over**. That is deliberate:
the events carry your commands, file paths and prompts, and keeping them out of
a file keeps the bridge host's blast radius small. `BRIDGE_ADMIN_HISTORY`
(default 200) caps the events kept per session; at most 50 sessions are kept,
the least recently active dropped first. `src/bridge_for_agents/store.py` is
the single place to add persistence if you ever want it.

Note this is separate from `BRIDGE_STATE`, which persists only the map from
session to Telegram topic id — never any event content.

### Access

`BRIDGE_ADMIN_TOKEN` guards the page, falling back to `BRIDGE_TOKEN` when
unset. Pass it once as `?token=…`; it is then kept in an `HttpOnly`,
`SameSite=Strict` cookie (marked `Secure` under TLS) so the secret leaves the
URL bar. A `Bearer` header works too, for scripts.

Like the hook endpoint, a loopback bind with no token configured is left open,
and the startup preflight refuses to serve the page on a non-loopback bind
without a token — it shows tool inputs and session history to anyone who can
reach it.

## Securing the Claude Code ↔ bridge channel

The hook endpoint is the sensitive surface: whatever can POST to it can approve
your tool calls. Two controls, both set up by `scripts/make-certs.sh`:

```bash
./scripts/make-certs.sh bridge.lan 10.0.0.5   # hostname in the hook URL + any extra IPs
```

It prints ready-to-paste exports and writes `pki/`:

| File | Goes to | Mode |
|---|---|---|
| `server.crt`, `server.key` | bridge host | key `0600` |
| `ca.crt` | every Claude Code host | `0644` |
| `ca.key` | keep offline, only needed to issue more certs | `0600` |

**1. Shared key.** `BRIDGE_TOKEN` is a 32-byte random hex string, checked with
`hmac.compare_digest` on every request. Claude Code sends it as
`Authorization: Bearer ${BRIDGE_TOKEN}`, interpolated from the environment
because `allowedEnvVars` lists it — so the secret never enters `settings.json`
and never reaches git. Keep it in your shell profile, a `systemd`
`EnvironmentFile` with mode `0600`, or your usual secret store.

**2. TLS.** With `BRIDGE_TLS_CERT` / `BRIDGE_TLS_KEY` set, the listener speaks
HTTPS (TLS 1.2 minimum) and the hook URL becomes `https://`. Without it the
bearer token crosses the LAN in cleartext, which defeats the point of having one.
Each Claude Code host trusts the CA via `NODE_EXTRA_CA_CERTS=/path/to/ca.crt`.
The hostname in the hook URL must match a SAN in the cert.

**Startup preflight.** The bridge exits rather than expose a weak listener: a
non-loopback bind with no token, a non-loopback bind with no TLS, or a token
under 32 chars all abort with an explanation. `BRIDGE_INSECURE=1` overrides for
lab use. A loopback-only bind needs neither.

Also worth doing:

- Firewall the port to your Claude Code hosts only.
- Rotate `BRIDGE_TOKEN` by restarting the bridge and updating each host; there's
  no rotation grace period, so do it when nothing is mid-approval.
- Deny is the safe answer when a prompt on your phone surprises you — it means
  something reached the bridge that you didn't start.

Not available: mutual TLS. Claude Code's HTTP hooks send no client certificate,
so the bearer token is the client's identity. If you need stronger, put an mTLS
reverse proxy in front and keep the bridge on loopback behind it.

## Network / private LAN

Works behind NAT with no port forwarding and no tunnel:

- **Outbound only, to Telegram.** Both directions are HTTPS to
  `api.telegram.org:443`. Pushing = `sendMessage`; receiving = `getUpdates` long
  polling (`timeout=30`, held open by Telegram, so replies arrive in ~a second).
- **No webhook.** The daemon calls `deleteWebhook` at startup — a bot can't use
  a webhook and `getUpdates` simultaneously.
- **Proxy**: the client uses `trust_env=True`, so `HTTPS_PROXY` / `NO_PROXY` are honoured.
- Dropped connections are retried by the poll loop (3 s backoff); a host resuming
  from sleep just reconnects.

### Bridge and Claude Code on different hosts

Set `BRIDGE_BIND` to the LAN address (or `0.0.0.0`) on the bridge host and point
`examples/hooks.settings.json` at `https://bridge.lan:8765/hook` — change the hostname to
match the SAN in your cert. Token and TLS setup is in
[Securing the channel](#securing-the-claude-code--bridge-channel) above; the
bridge won't start on a network interface without both. Never expose this port
to the internet.

Only the later Twilio/WhatsApp channel needs a public inbound webhook — that one
does require a tunnel (`cloudflared` / ngrok).

## Security

- Daemon binds `127.0.0.1` by default; a non-loopback `BRIDGE_BIND` requires a
  token and TLS (see above). Only `TG_CHAT_ID` is trusted; other chats are ignored.
- Keep the bot token out of settings files — it lives only in the daemon's env.
- Consider `Deny` as the safe default if you leave `bypassPermissions` off.

## Project layout

```
src/bridge_for_agents/
  bridge.py            the daemon: channels, hook handlers, HTTP endpoint
  store.py             in-memory session/event history behind the admin page
  admin.py             read-only /admin routes and page
  cli.py               console entry point (bridge-for-agents)
examples/
  hooks.settings.json  Claude Code hook config to merge into your settings
scripts/
  make-certs.sh        local CA, server cert, shared secret
tests/                 pytest suite
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

### Trying it without a bot

`TG_API_BASE` overrides the Bot API root (default `https://api.telegram.org`),
so the bridge can be pointed at a stand-in server. The sibling
[bridge-for-agent-test](../bridge-for-agent-test) project is exactly that: a
fake Telegram with a browser "phone" whose buttons feed real `callback_query`
updates back through `getUpdates`, sample payloads for every hook event, a
ready-made `.claude/settings.json`, and an end-to-end check.

```bash
cd ../bridge-for-agent-test
./bin/up.sh        # fake phone + bridge
./bin/smoke.py     # automated end-to-end check
```

## Known gaps / next steps

Fuller reasoning, with priorities, is in [ROADMAP.md](ROADMAP.md).

- `multiSelect` questions are answered single-choice (MVP).
- **Sending a *new* prompt into a running interactive session is not possible
  via hooks.** Options for v2: (a) `tmux send-keys` into the session pane,
  (b) let the bridge run follow-ups headless: `claude -p --resume <session_id> "<text>"`
  and stream the result back — the `session_id` is already in every hook payload.
- Run as a service: `systemd --user` unit or a Docker container on the same host
  as Claude Code (hooks post to localhost).

## Adding WhatsApp (Twilio) later

Implement `Channel` (`send`, `ask`) in a `TwilioChannel`:
- `ask` sends the text with numbered options; the reply "1"/"2"/free text resolves the future.
- Twilio needs a public webhook for inbound messages → expose the bridge via
  `cloudflared tunnel` / ngrok and add `POST /twilio` that validates the Twilio signature.
- Both channels can run at once; route by session or by a `BRIDGE_CHANNEL` env var.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
and the [Code of Conduct](CODE_OF_CONDUCT.md). For anything security-related,
follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

[MIT](LICENSE) © 2026 Roman Marek
