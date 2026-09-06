# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Read-only web admin page at `/admin`: outstanding prompts with a countdown,
  a session list, and the event timeline with outcomes and timings. Enabled
  with `BRIDGE_ADMIN=1`, guarded by `BRIDGE_ADMIN_TOKEN` (falls back to
  `BRIDGE_TOKEN`), served as JSON at `/admin/api/state`.
- `store.py`: bounded in-memory session and event history. Nothing is written
  to disk, so history is lost on restart; `BRIDGE_ADMIN_HISTORY` (default 200)
  caps events per session.
- The startup preflight now also refuses to serve the admin page on a
  non-loopback bind without a token.
- `TG_API_BASE` to override the Bot API root, so the bridge can be driven by a
  stand-in Telegram — see the `bridge-for-agent-test` project.

- Secret redaction (`redact.py`), on by default: recognisable credentials are
  removed from tool inputs before they reach the chat or the admin page, and
  the message reports how many were hidden. Redaction runs before the
  600-character truncation, so a key cannot survive by straddling the cut.
  `BRIDGE_REDACT=0` disables it; `BRIDGE_REDACT_EXTRA` points at a file of
  extra regexes. Best effort by design — no entropy scoring, which would flag
  git SHAs and base64 and get itself switched off.
- Mermaid diagrams, rendered by GitHub: connection directions, one approval
  end to end, the fail-safe decision tree, and a topology decision tree.
- `SECURITY-MODEL.md`: who starts each connection, what the chat can and
  cannot make the host do, who is trusted with what, the fail-safe guarantee,
  and a pre-deployment checklist.
- `ROADMAP.md`, and a README **Deployment** section: how bots, groups and
  bridges map onto machines, and which topology to pick.

- `notify_user`, an MCP tool letting a running agent push a progress line to
  the user's phone (`bridge-for-agents-mcp`, backed by a new `POST /notify`).
  Send-only: it calls `send`, never `ask`, awaits nothing and returns nothing
  from the chat. Redacted, audited with the message in full, and capped by
  `BRIDGE_NOTIFY_RATE` (20/min); `BRIDGE_NOTIFY=0` refuses them. The JSON-RPC
  is hand-written — the MCP SDK brings ~25 transitive dependencies for a
  one-tool stdio server.
- Telegram service messages — a created or closed forum topic, a title change
  — are no longer recorded as unsolicited inbound. Found on a real run: opening
  a topic filed a rejection 170 ms later, so the log that exists to say
  "someone tried to initiate something" gained one false entry per topic.
- `skills/notify-user/`: a Claude Code skill covering how to write a
  notification for a lock screen, and what must never go in one.
- Diagnostic logging (`logs.py`), distinct from the audit log and with the
  opposite rule about secrets: `BRIDGE_LOG_LEVEL`, `BRIDGE_LOG_FILE` (mode
  `0600`, rotating at 10 MB, 5 kept, permissions preserved across rollover) and
  `BRIDGE_LOG_ACCESS`. Every record passes through the redactor, applied as a
  logging filter so it covers all handlers. DEBUG now reports Telegram calls,
  inbound update ids and prompts opening; previously the level was fixed at
  INFO and there were no debug statements at all.
- Audit log (`audit.py`): append-only JSONL at `BRIDGE_AUDIT`, mode `0600`,
  `fsync` per write, sequence and chain continuing across restarts. Records
  `bridge_start` (config fingerprint, no secrets), `request_open` with the
  **full unredacted** tool input, `decision` with outcome, `source`
  (`button`/`text`/`timeout`/`terminal`) and latency, `rejected_unsolicited`,
  `auth_failure` and `bridge_stop`. `BRIDGE_AUDIT_KEY` chains each record with
  an HMAC; `bridge-audit-verify` recomputes the chain, names the first bad
  sequence number and exits non-zero. An audit failure never takes a decision
  down with it.
- `PLAN.md`: the bridge is a one-way approval channel, and the invariant that
  makes it one. `tests/test_invariant.py` enforces it.

### Changed

- The invariant in `PLAN.md` now reads "originates on the Claude Code host —
  from a hook, or from a tool the agent called there", widened once and
  deliberately to admit `notify_user`. The half that matters is unchanged:
  nothing the chat sends can start anything.

- aiohttp's per-request access log is **off by default**. The admin page polls
  every two seconds, so it wrote about 1,800 lines an hour. `BRIDGE_LOG_ACCESS=1`
  restores it.

- **Breaking:** `/ping` and `/pending` are gone. They were chat-initiated
  actions — harmless individually, but the precedent that makes the invariant
  negotiable.
- `TelegramChannel._on_update` is now a single guard: an update either answers
  a request that is currently open, or it is dropped and recorded. There is no
  other branch.

- A prompt nobody answered is now reported as `timeout` on the admin page,
  distinct from `terminal` when *Answer in terminal* was chosen. Both still
  return `{}` to Claude Code, so behaviour is unchanged.

## [0.1.0] - 2026-09-05

Initial release — MVP.

### Added

- Relay of Claude Code hook events to Telegram and the decision back to Claude
  Code: `PermissionRequest` (Allow / Deny / Terminal), `PreToolUse` for
  `AskUserQuestion` (one button per option), `Notification` on `idle_prompt`,
  `Stop`, and `SessionEnd`.
- Free-text replies: an answer on a question, a deny-with-reason on a permission.
- Session separation through Telegram forum topics, with `BRIDGE_SCOPE` of
  `session`, `project` or `flat`, and a persisted topic map in `BRIDGE_STATE`.
- Bearer-token authentication and TLS on the hook endpoint, with a startup
  preflight that refuses to expose a weak listener.
- `scripts/make-certs.sh` to generate a local CA, a server certificate and the
  shared secret.
- `Channel` abstraction as the extension point for further chat transports.

[Unreleased]: https://github.com/spazyCZ/bridge-for-agents/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/spazyCZ/bridge-for-agents/releases/tag/v0.1.0
