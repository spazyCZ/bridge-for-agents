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

Step by step in [INSTALL.md](INSTALL.md). In brief:

1. **Bot**: `@BotFather` → `/newbot` → copy the token.
2. **Chat id**: message the bot, then
   `curl https://api.telegram.org/bot<TOKEN>/getUpdates` → `message.chat.id`.
3. **Run it** (Python 3.11+):
   ```bash
   pip install bridge-for-agents
   export TG_BOT_TOKEN=123:abc  TG_CHAT_ID=-1001234567890
   bridge-for-agents
   ```
   A card appears in the chat naming the host, bot and version.
4. **Hook it up**: merge `examples/hooks.settings.json` into
   `~/.claude/settings.json` (or a project's `.claude/settings.json`). Confirm
   with `/hooks` in Claude Code.

On one machine that is the whole setup: the bridge binds `127.0.0.1`, so no
token, TLS or certificates are required. For a group with per-session topics,
or for more than one machine, see the [user guide](USER-GUIDE.md).

## What you get

| | |
|---|---|
| **Permission prompts** | tool and command, with Allow / Deny / Answer in terminal |
| **Questions** | `AskUserQuestion` as buttons, one per option |
| **Typed replies** | `y` / `n`, or an option number with a comment — `2 - check the migration first` |
| **Notifications** | an MCP tool so a long task can report progress to your phone |
| **A topic per session** | parallel sessions stay readable months later |
| **Admin page** | what is waiting right now, and what was decided |
| **Audit log** | every request and decision, optionally HMAC-chained |
| **Redaction** | credentials stripped before anything leaves the machine |

If nobody answers, the bridge returns `{}` and Claude Code prompts in the
terminal exactly as it would have. Every failure path does the same — a
timeout, an exception, Telegram being down. **No error path can approve
anything.**

## Documentation

| | |
|---|---|
| [INSTALL.md](INSTALL.md) | the shortest path from nothing to a prompt on your phone |
| [USER-GUIDE.md](USER-GUIDE.md) | setting up Telegram, deployment, notifications, logs, audit, **troubleshooting** |
| [SECURITY-MODEL.md](SECURITY-MODEL.md) | who starts each connection, what the chat can and cannot do, a pre-deployment checklist |
| [SECURITY.md](SECURITY.md) | reporting a vulnerability, and operating advice |
| [PLAN.md](PLAN.md) | the one-way invariant and where the project is going |
| [ROADMAP.md](ROADMAP.md) | what is planned, and what is deliberately not |
| [CONTRIBUTING.md](CONTRIBUTING.md) | development setup and the checks CI runs |

## Security in one paragraph

Both directions to Telegram are outbound, so **nothing on the internet can
connect to your machine** — receiving an answer is the bridge holding a
`getUpdates` request open. The chat cannot start anything; it can only answer a
hook Claude Code already raised. Everyone in the chat can approve, and a
free-text reply reaches Claude as text it reads, so keep the membership to
yourself and the bot. Full detail in
[SECURITY-MODEL.md](SECURITY-MODEL.md).

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

## Status

Pre-1.0 and in use. [PLAN.md](PLAN.md) sets the direction — a single-user,
one-way approval channel with a provable record — and [ROADMAP.md](ROADMAP.md)
lists what is next, including the things that will not be built because they
would break the invariant.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
and the [Code of Conduct](CODE_OF_CONDUCT.md). For anything security-related,
follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

[MIT](LICENSE) © 2026 Roman Marek
