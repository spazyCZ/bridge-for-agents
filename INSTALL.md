# Install

The shortest path from nothing to a permission prompt on your phone. For
anything beyond that — groups, topics, several machines, TLS — see the
[user guide](USER-GUIDE.md).

Python 3.11 or newer.

## 1. Install

```bash
pip install bridge-for-agents
```

<details>
<summary>Testing a pre-release from TestPyPI</summary>

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            bridge-for-agents
```

The extra index matters: TestPyPI does not mirror `aiohttp`, so without it the
dependency will not resolve.
</details>

<details>
<summary>From a checkout</summary>

```bash
git clone https://github.com/spazyCZ/bridge-for-agents
cd bridge-for-agents
pip install .
```
</details>

## 2. Make a bot

`@BotFather` → `/newbot` → copy the token.

Then message your new bot anything, and read the chat id back:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"id":[-0-9]*' | head -1
```

A direct chat gives a positive id and works fine. A group gives a negative one
and gets you a topic per session — that setup is in the
[user guide](USER-GUIDE.md#setting-up-telegram), including the two traps
(privacy mode, and the *Manage Topics* admin right).

## 3. Run it

```bash
export TG_BOT_TOKEN=123456:ABC-your-token
export TG_CHAT_ID=987654321
bridge-for-agents
```

A card appears in the chat naming the host, bot and version. That is the whole
configuration on one machine: the bridge binds `127.0.0.1`, so no shared
secret, no TLS and no certificates are needed.

Keep the token in your shell profile or a `0600` file. Never in a settings
file, never in git.

## 4. Point Claude Code at it

Merge this into `~/.claude/settings.json`, or a project's
`.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook",
                     "timeout": 3 } ] }
    ],
    "PermissionRequest": [
      { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook",
                     "timeout": 600,
                     "statusMessage": "Waiting for Telegram approval…" } ] }
    ],
    "PreToolUse": [
      { "matcher": "AskUserQuestion",
        "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook",
                     "timeout": 600 } ] }
    ],
    "Stop":         [ { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook", "timeout": 10 } ] } ],
    "SessionEnd":   [ { "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook", "timeout": 10 } ] } ],
    "Notification": [ { "matcher": "idle_prompt",
                        "hooks": [ { "type": "http", "url": "http://127.0.0.1:8765/hook", "timeout": 10 } ] } ]
  }
}
```

The full file is in [`examples/hooks.settings.json`](examples/hooks.settings.json).

`UserPromptSubmit` is what tells the bridge which session is running where.
Without it, notifications from `notify_user` cannot be attributed to a
session and land in the group's General topic instead of the session's own.
Run `/hooks` in Claude Code to confirm they loaded.

The hook `timeout` must stay **above** `BRIDGE_TIMEOUT` (540 s by default), so
the bridge always answers before Claude Code gives up waiting for it.

## 5. Try it

Ask Claude Code to do something that needs permission. The prompt appears on
your phone with Allow / Deny buttons; you can also reply `y` or `n`, or type a
sentence to deny with that as the reason.

If nobody answers within `BRIDGE_TIMEOUT`, the bridge stands aside and Claude
Code prompts in your terminal exactly as it would have.

## Optional, one variable each

| | |
|---|---|
| `BRIDGE_ADMIN=1` | a read-only dashboard at `http://127.0.0.1:8765/admin` |
| `BRIDGE_AUDIT_KEY=$(openssl rand -hex 32)` | HMAC-chain the audit log so tampering is detectable |
| `BRIDGE_TIMEOUT=120` | shorter wait before falling back to the terminal |
| `BRIDGE_SCOPE=project` | one topic per repository instead of per session |

## The skills

Two Claude Code skills ship with the package. `notify-user` covers writing a
notification and what must never go in one; `bridge-ops` covers running and
diagnosing the bridge, and how to behave while it is gating you.

```bash
bridge-for-agents install-skills     # into ~/.claude/skills
bridge-for-agents list-skills        # what this build carries
```

Installing again leaves anything already there alone, so an edited skill
survives an upgrade; `--force` replaces them. Start a new Claude Code session
to pick them up.

Notifications from a running agent need one more step:

```bash
claude mcp add bridge-notify -- bridge-for-agents-mcp
```

## If it does not work

- `curl localhost:8765/health` — is the bridge up?
- `/hooks` in Claude Code — did the hooks load?
- The startup card in the chat — right bot, right chat?

The [troubleshooting chapter](USER-GUIDE.md#troubleshooting) covers the rest,
including nothing arriving, answers not resolving, and everything landing in
the wrong place.

## Before pointing it at anything real

Read [SECURITY-MODEL.md](SECURITY-MODEL.md). The short version: everyone in the
chat can approve your tool calls, and a typed reply reaches Claude as text it
reads — so keep the chat to yourself and the bot.
