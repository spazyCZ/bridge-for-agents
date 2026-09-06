# Configuring the bridge

## Required

| | |
|---|---|
| `TG_BOT_TOKEN` | from BotFather. No default; the bridge will not import without it |
| `TG_CHAT_ID` | negative for a supergroup, positive for a direct chat |

On one machine that is the whole configuration. The bridge binds `127.0.0.1`,
so there is no shared secret, no TLS and no certificate to manage.

## Commonly worth setting

| | |
|---|---|
| `BRIDGE_SCOPE` | `session` (default), `project`, or `flat` |
| `BRIDGE_TIMEOUT` | seconds before falling back to the terminal, default 540 — must stay **below** the hook `timeout` |
| `BRIDGE_ADMIN=1` | read-only dashboard at `/admin` |
| `BRIDGE_AUDIT` | audit log path, or `off`. Default `~/.local/state/claude-bridge/audit.jsonl` |
| `BRIDGE_AUDIT_KEY` | HMAC-chain the audit log so tampering is detectable. Store it **away** from the log |
| `BRIDGE_LOG_LEVEL` | `DEBUG` reports every Telegram call, inbound update and prompt |
| `BRIDGE_LOG_FILE` | also log to a rotating `0600` file. Redacted, unlike the audit log |

## Only off loopback

| | |
|---|---|
| `BRIDGE_BIND` | LAN address or `0.0.0.0`. Requires the next two |
| `BRIDGE_TOKEN` | shared secret, 32+ characters, sent as `Authorization: Bearer` |
| `BRIDGE_TLS_CERT` / `BRIDGE_TLS_KEY` | from `scripts/make-certs.sh` |
| `BRIDGE_ADMIN_TOKEN` | required if the admin page is on off loopback |
| `BRIDGE_INSECURE=1` | overrides the preflight. Lab networks only |

## Notifications

| | |
|---|---|
| `BRIDGE_NOTIFY=0` | refuse agent-sent notifications entirely |
| `BRIDGE_NOTIFY_RATE` | most per minute, default 20 — a runaway-loop guard, not a target |

## Redaction

| | |
|---|---|
| `BRIDGE_REDACT=0` | send tool inputs unredacted. Rarely what you want |
| `BRIDGE_REDACT_EXTRA` | file of extra regexes, one per line, `#` for comments |

Redaction is best effort. It knows common credential shapes; it does not know
your internal hostnames or a customer's phone number.

## Which deployment

| Machines | Shape | Needs |
|---|---|---|
| One | one bot, one bridge on loopback | nothing else |
| Several | **a bridge per machine, own bot each, one shared group** | nothing else — every listener stays on loopback |
| Several, centrally managed | one bridge on the LAN | `BRIDGE_TOKEN` **and** TLS, plus the CA on every host |

A bridge per machine is usually right: no shared secret, no certificates, no
reachable port. The cost is one `/newbot` per machine.

The ratios that settle the rest: **one bot per bridge process** (`getUpdates`
is exclusive per token), **many bridges per group** (several bots can share
one), and **many sessions per bridge** (that is what topics are for).

The one configuration that fails silently is the same token on two machines.

## Rotating the bot token

**It cannot be automated.** The Bot API has no revoke or regenerate method;
only `@BotFather` does, and it is a chat. Driving a user account over MTProto
to talk to it is possible and a bad trade — you would store a full account
session, able to read every chat, to rotate a strictly weaker credential.

1. `@BotFather` → `/revoke` → pick the bot → copy the new token.
2. Update wherever the environment comes from, at mode `0600`.
3. Restart the bridge; it holds the old token in memory.

**Check the old token is dead.** `/token` shows the existing token while
`/revoke` replaces it — confuse the two and you have a working new token, a
still-working old one, and no rotation.

```bash
curl -s "https://api.telegram.org/bot$OLD_TOKEN/getMe"   # want ok:false
```

`bridge-for-agent-test/bin/rotate-token.sh` does all of this, including the
old-token check and a timestamped backup of the previous `.env`.
