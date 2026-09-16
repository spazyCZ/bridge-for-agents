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

- `bridge-for-agents install-skills` and `list-skills`. The skills now travel
  inside the wheel, so a `pip install` user can get them at all — before this
  they existed only in a repository they had no reason to clone, while the docs
  told them to `cp` from it. Installing twice leaves an edited skill alone
  unless `--force` is given, and a checkout falls back to the repository root,
  so contributors can install the skills they are editing.
- `skills/bridge-ops/`: a Claude Code skill for operating and diagnosing the
  bridge, and for behaving well while gated by it. Leads with five hazards an
  agent will otherwise hit — printing the audit log, which deliberately holds
  unredacted secrets; restarting the bridge that is approving the restart;
  starting a second process on one token; printing the configuration to
  diagnose it; and adding a chat-initiated feature the invariant refuses.
  Three reference files carry setup, diagnosis and configuration.
- `INSTALL.md`: the shortest path from nothing to a prompt on the phone, with
  the hook JSON inline and the TestPyPI extra-index-url that people miss.
- Publishing workflows. A push to `test` puts a unique `.devN` build on
  TestPyPI; a `v*` tag publishes to PyPI. Both gate on CI and use Trusted
  Publishing, so no API token is stored. The release job refuses a tag that
  disagrees with `__version__`, refuses a version already on PyPI, and runs
  `twine check` before uploading.
- `USER-GUIDE.md`: setting up Telegram (including the privacy-mode and
  Manage-Topics traps people hit), deployment, answering prompts,
  notifications, the admin page, logs, audit, securing the channel, and a
  troubleshooting chapter that did not exist before. The README is now a
  131-line orientation page rather than a 577-line manual; no content was lost
  and a check confirms every command from the old README still appears.
- The startup message is now an identifying card in the group's **General**
  topic: host, version, bot username, chat id and whether it is a forum, and
  the scope and answer window. With one bridge per machine sharing a group,
  this is what says which one came online. Identity only — no security posture
  and no listener address, since a chat is the wrong place to publish which
  controls are off; the full fingerprint stays in the audit log's
  `bridge_start` record. A `🔴 bridge offline` note follows a clean shutdown.
- A `getUpdates` 409 now logs an explicit error and warns the group once that
  two bridges share the bot token. It previously surfaced as a generic warning,
  leaving the one misconfiguration that is wrong rather than broken invisible.
- Typed replies are parsed rather than passed through (`replies.py`). `y`/`yes`
  allows and `n`/`no` denies, a negative keeps its reason (`no, wrong branch`),
  and on a question a leading option number picks that option with anything
  after a separator kept as a comment (`2 - but check the migration first`).
  A number without a separator stays an answer, so `3 replicas` is not a vote.
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

### Fixed

- **Every notification landed in General instead of its session's topic.**
  `notify_user` read `CLAUDE_SESSION_ID`, which Claude Code does not set — it
  exposes no session id to MCP servers at all — so the session was always
  empty, `thread_for` was never called, and the code had been dead since it was
  written. It failed silently because General is a working destination. The
  bridge now infers the session from the working directory, matched against the
  live sessions the hooks have already reported, and says so at INFO when it
  cannot. The MCP server sends `CLAUDE_PROJECT_DIR`, which Claude Code does
  set and which matches the `cwd` hooks report, rather than `getcwd()`;
  `BRIDGE_SESSION_ID` overrides the inference.

- **A comment on a question was corrupting the answer.** `answers` maps a
  question to the *selected option label*; the comment feature appended the
  remark to it, so `2 - but check the migration first` sent
  `"SQLite — but check the migration first"` where a label was expected. The
  remark now travels in `additionalContext` and `answers` stays a bare label.
  Found by checking the shapes against the hooks specification.

- **The shutdown path had never run.** SIGTERM — what `kill` and systemd send —
  terminates Python where it stands unless a handler is installed, so the
  `finally` block was dead code: no `🔴 bridge offline` notice and no
  `bridge_stop` audit record, across five bridge lifetimes. Handlers for
  SIGTERM and SIGINT now stop the loop so shutdown actually happens. A missing
  offline notice now means what it should: the bridge died badly, since SIGKILL
  and the OOM killer remain uncatchable.

### Changed

- Documentation corrected: returning `{}` means *no decision*, which is a
  terminal prompt in an interactive session but a **denial** in one that cannot
  prompt — a background subagent, or headless. Both are safe; the docs
  described only the first.

- The package version is now single-sourced from
  `src/bridge_for_agents/__init__.py`, with `pyproject.toml` reading it. It was
  duplicated in both, which is how a release ends up disagreeing with its tag.

- **Replying `yes` to a permission used to deny**, with "yes" as the reason.
  It now allows. Hedged affirmatives (`yes but only the first one`) still deny
  and carry the caveat — approving on a misread runs the command, denying only
  returns the prompt to the terminal.
- Typing the word `ask` fell back to the terminal, because it matched the
  *button value* for "Answer in terminal". Only a button press means terminal.

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
