# bridge-for-agents

[![CI](https://github.com/spazyCZ/bridge-for-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/spazyCZ/bridge-for-agents/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Answer Claude Code and Codex permission prompts from Telegram. Claude Code
questions are supported too. No Remote Control needed — built on agent hooks.

```
Claude Code ──HTTP hook──────▶ bridge ──Bot API──▶ Telegram
Codex ──command hook relay──▶ bridge ◀──button/reply── you
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
4. **Hook it up**: for Claude Code, merge `examples/hooks.settings.json` into
   its settings; for Codex, run `bridge-for-agents install-codex-hooks`.
   Confirm with `/hooks` in either client.

On one machine that is the whole setup: the bridge binds `127.0.0.1`, so no
token, TLS or certificates are required. For a group with per-session topics,
or for more than one machine, see the [user guide](USER-GUIDE.md).

## What you get

| | |
|---|---|
| **Permission prompts** | tool and command, with Allow / Deny / Answer in terminal |
| **Questions (Claude Code)** | `AskUserQuestion` as buttons, one per option |
| **Typed replies** | `y` / `n`, or an option number with a comment — `2 - check the migration first` |
| **Notifications** | an MCP tool so a long task can report progress to your phone |
| **A topic per session** | parallel sessions stay readable months later |
| **Admin page** | what is waiting right now, and what was decided |
| **Audit log** | every request and decision, optionally HMAC-chained |
| **Redaction** | credentials stripped before anything leaves the machine |

If nobody answers, the bridge returns `{}` — *no decision* — and the agent
client uses its normal local approval flow. A session that cannot prompt may
deny the call instead. Every failure path does the same: a timeout, an
exception, Telegram being down. **No error path can approve anything.**

## Documentation

| | |
|---|---|
| [INSTALL.md](INSTALL.md) | the shortest path from nothing to a prompt on your phone |
| [USER-GUIDE.md](USER-GUIDE.md) | setting up Telegram, deployment, notifications, logs, audit, **troubleshooting** |
| [SECURITY-MODEL.md](SECURITY-MODEL.md) | who starts each connection, what the chat can and cannot do, a pre-deployment checklist |
| [SECURITY.md](SECURITY.md) | reporting a vulnerability, and operating advice |
| [PLAN.md](PLAN.md) | the one-way invariant and where the project is going |
| [ROADMAP.md](ROADMAP.md) | what is planned, and what is deliberately not |
| [skills/](skills/) | two agent skills, installed with `bridge-for-agents install-skills`: `notify-user` for writing a notification, `bridge-ops` for running and diagnosing the bridge |
| [CONTRIBUTING.md](CONTRIBUTING.md) | development setup and the checks CI runs |

## Security in one paragraph

Both directions to Telegram are outbound, so **nothing on the internet can
connect to your machine** — receiving an answer is the bridge holding a
`getUpdates` request open. The chat cannot start anything; it can only answer a
hook the agent already raised. Everyone in the chat can approve, and a
free-text reply reaches the agent as text it reads, so keep the membership to
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
  codex.hooks.json     Codex hook config, using the packaged relay command
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

### Testing without Telegram

The test suite replaces the Telegram Bot API with a local stand-in, including
the Codex stdin/stdout relay. It needs no bot token or network access:

```bash
pytest -q
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
