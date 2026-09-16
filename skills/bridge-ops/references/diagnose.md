# Diagnosing the bridge

One row per symptom. Each gives the single check that separates the likely
causes, so you are not running all of them.

Remember the three sources answer different questions: the **admin page** what
the bridge thinks is happening, the **diagnostic log** what it tried, the
**audit log** what was actually decided. Do not print the audit log directly —
it holds unredacted tool inputs.

## Nothing reaches the phone

| Check | Command | What it means |
|---|---|---|
| Bridge up? | `curl -s localhost:8765/health` | no answer → not running, or a different port |
| Did Claude Code reach it? | `curl -s localhost:8765/admin/api/state \| python3 -c "import json,sys;print(json.load(sys.stdin)['totals'])"` | `events: 0` → the problem is upstream of the bridge |
| Hooks loaded? | `/hooks` in Claude Code | absent → merge `examples/hooks.settings.json` |
| Hook URL matches? | compare `settings.json` with the port in the startup card | a changed `BRIDGE_PORT` breaks it silently |
| Right chat? | the startup card in General | names bot, chat id and scope |
| Two bridges? | `ps -eo pid,comm,args --no-headers \| awk '$2 ~ /python/ && /-m bridge_for_agents/'` | two → answers land at random |
| Webhook set? | `curl -s ".../bot$TG_BOT_TOKEN/getWebhookInfo"` | a webhook and `getUpdates` are mutually exclusive; the bridge clears it at startup |

Events arriving but nothing on the phone → the bridge-to-Telegram side. No
events → the Claude-Code-to-bridge side. That split saves most of the work.

## Answers do not resolve

- **Was it a reply?** The bridge matches typed answers by
  `reply_to_message_id`. A loose message in the chat answers nothing and is
  recorded as unsolicited — visible on the admin page as `rejected`, and in the
  audit log as `rejected_unsolicited`.
- **Had it already expired?** Tapping an old button answers *"This request
  already expired"*. The decision returned to the terminal at
  `BRIDGE_TIMEOUT`.
- **Two bridges on one token?** The group gets a one-time warning naming the
  bot and a host. Stop one.
- **Was the outcome `terminal` or `timeout`?** They look identical from
  outside and mean different things — `terminal` is a deliberate "answer at the
  keyboard", `timeout` is nobody saw it. Only the audit log distinguishes them.

## Notifications in General, but prompts in their own topics

Two different faults, and the split tells you which. Prompts getting topics
while only notifications do not means topic creation works and the notification
could not be attributed to a session.

MCP servers are never told their session id — Claude Code exposes no such
variable — so the bridge infers it from the working directory, matched against
the live sessions the hooks reported. General means nothing matched:

```
notification going to the general thread: no live session for cwd '/repo'
```

Usual causes: the session had not sent a hook yet, the MCP server's directory
differs from the `cwd` the hooks report, or the sender was a script rather than
Claude Code. `BRIDGE_SESSION_ID` overrides the inference.

## Everything lands in General, no topics

The bot is in the group but is not an admin with **Manage Topics**:

```bash
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/getChatMember?chat_id=$TG_CHAT_ID&user_id=${TG_BOT_TOKEN%%:*}"
```

`status` must be `administrator` and `can_manage_topics` `true`. A
`400 not enough rights to create a topic` says the same. Also confirm the chat
really is a forum — the startup card says so, and the bridge logs a warning at
startup when it is not.

## The bridge will not start

The preflight refuses an unsafe listener and names the rule:

| Message | Fix |
|---|---|
| non-loopback bind, no `BRIDGE_TOKEN` | set one, or bind `127.0.0.1` |
| non-loopback bind, no TLS | `scripts/make-certs.sh`, or bind loopback |
| token under 32 characters | `openssl rand -hex 32` |
| admin enabled off loopback, no admin token | set `BRIDGE_ADMIN_TOKEN` |

`BRIDGE_INSECURE=1` overrides all of them and is for a lab network only.

Other startup failures: `address already in use` means a bridge is already
running — find it before starting another. A missing `TG_BOT_TOKEN` or
`TG_CHAT_ID` raises `KeyError` at import; both are required with no default.

## The bridge went quiet with no offline note

A clean stop posts `🔴 bridge offline` and writes a `bridge_stop` record. A
missing note means it died badly — `kill -9`, the OOM killer, or power loss are
all uncatchable.

```bash
dmesg -T | grep -i "killed process"           # OOM?
grep -c bridge_start audit.jsonl; grep -c bridge_stop audit.jsonl
```

Unequal counts mean at least one bridge died without unwinding.

## Prompts time out while the user is away

`BRIDGE_TIMEOUT` (540 s default) must stay **below** the hook `timeout` in
`settings.json`, so the bridge always answers before Claude Code stops waiting
for it. Raise both together, and remember a timed-out prompt returns to a
terminal that may have nobody at it.

## Something arrived that nobody started

Deny it, then look:

```bash
bridge-audit-verify                      # chain intact?
bridge-audit-verify --session <id>       # that session's history
```

`auth_failure` records show attempts to POST without a valid token.
`rejected_unsolicited` records show chat messages that answered nothing. Both
are signals rather than noise. Remember that everyone in the chat can approve,
and that a typed answer reaches Claude as text it acts on — if the membership
is wrong, fix that first.
