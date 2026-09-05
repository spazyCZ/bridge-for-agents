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

- `ROADMAP.md`, and a README **Deployment** section: how bots, groups and
  bridges map onto machines, and which topology to pick.

### Changed

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
