# Security policy

## Reporting a vulnerability

Report privately through GitHub: **Security → Report a vulnerability** on this
repository. Please do not open a public issue for a suspected vulnerability.

Include the version or commit, your configuration (with secrets removed), and
the steps to reproduce. You can expect an acknowledgement within a few days.

## Supported versions

This project is pre-1.0. Only the latest commit on `main` receives fixes.

## Threat model

A fuller, user-facing version of this — who initiates each connection, what the
chat can and cannot do, and a checklist to run before deploying — is in
[SECURITY-MODEL.md](SECURITY-MODEL.md).


The hook endpoint is the sensitive surface: **whatever can POST to it can
approve your tool calls.** Two controls guard it, both produced by
`scripts/make-certs.sh`:

- `BRIDGE_TOKEN` — a 32-byte random hex secret compared with
  `hmac.compare_digest` on every request, sent by Claude Code as
  `Authorization: Bearer …` and interpolated from the environment so it never
  enters `settings.json` or git.
- `BRIDGE_TLS_CERT` / `BRIDGE_TLS_KEY` — TLS 1.2 minimum. Without them the
  bearer token crosses the network in cleartext.

The daemon refuses to start on a weak configuration: a non-loopback bind with
no token, a non-loopback bind with no TLS, or a token under 32 characters.
`BRIDGE_INSECURE=1` overrides this for lab use only.

The admin page at `/admin` is a second surface. It is **off unless
`BRIDGE_ADMIN=1`**, read-only (no approval path in the browser), and guarded by
`BRIDGE_ADMIN_TOKEN` — falling back to `BRIDGE_TOKEN` — with the same loopback
allowance and the same preflight refusal off loopback. It exposes tool inputs
and session history, so treat its token as equally sensitive. The history it
serves is process memory only and is never written to disk.

On the chat side, only messages from `TG_CHAT_ID` are trusted; every request
carries a random id embedded in its buttons, so an answer resolves exactly the
request it belongs to.

Not available: mutual TLS — Claude Code's HTTP hooks send no client
certificate, so the bearer token is the client's identity. Put an mTLS reverse
proxy in front and keep the bridge on loopback if you need more.

## Operating advice

- Never expose the bridge port to the internet. Firewall it to your Claude Code
  hosts.
- Keep `TG_BOT_TOKEN` and `BRIDGE_TOKEN` in your shell profile, a systemd
  `EnvironmentFile` with mode `0600`, or your usual secret store — never in a
  settings file and never in git.
- Rotate `BRIDGE_TOKEN` by restarting the bridge and updating each host. There
  is no rotation grace period, so rotate when nothing is mid-approval.
- `pki/`, `*.key` and `*.crt` are gitignored. Keep `ca.key` offline.
- Leave `BRIDGE_ADMIN` off where you do not need it, and give the admin page
  its own token rather than reusing `BRIDGE_TOKEN` when you can.
- Deny is the safe answer when a prompt surprises you on your phone — it means
  something reached the bridge that you did not start.
