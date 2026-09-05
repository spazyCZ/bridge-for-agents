# Roadmap

Where the bridge goes next, roughly in the order the work is worth doing.
Nothing here is committed to a release; it is a record of what has been
considered and why.

Tiers are about **value per unit of effort**, not difficulty. Anything in
Tier 1 is small and changes the product materially.

Shipped so far: **secret redaction** — recognisable credentials are removed
from tool inputs before they leave the host. See
[SECURITY-MODEL.md](SECURITY-MODEL.md#what-is-enforced-today).

## At a glance

Effort is rough: **S** is an afternoon, **M** a day or two, **L** a design
question before any code.

| Tier | What | Why it matters | Effort |
|---|---|---|---|
| 1 | [Approver allowlist](#approver-identity-tg_allowed_users) | Presses are checked against the *chat*, never the person — every group member can approve, and steer | **S** |
| 1 | [Auto-allow rules](#auto-allow-rules) | Thirty file reads means thirty notifications; the main reason to give up on it | **M** |
| 2 | [Context on the prompt](#context-why-is-claude-asking) | You approve a one-line command with no idea why it was asked | **S–M** |
| 2 | [Recover a timed-out prompt](#recovering-a-timed-out-prompt) | After the timeout the call waits on a terminal nobody is watching | **M** |
| 2 | [Coalesce bursts](#coalescing-bursts) | Five prompts in three seconds should be one message | **M** |
| 3 | [Send a new prompt](#tier-3--sending-a-new-prompt-into-a-session) | Turns the bridge from reactive to interactive — and changes the security model | **L** |
| 4 | systemd unit and Dockerfile | Answers "how do I keep this running" | **S** |
| 4 | Metrics on the admin page | `duration_ms` and outcomes are already recorded; shows whether rules are tuned | **S** |
| 4 | Webhook mode | Lifts one-poller-per-token, but needs public inbound | **L** |
| 5 | Edit the message instead of replying | Halves chat volume | **S** |
| 5 | `/sessions`, `/mute`, `/deny_all` | A panic button, and a way to look around | **S** |
| 5 | Silence non-decisions | Ring for permissions, not for `Stop` | **S** |
| 5 | `multiSelect` questions | Currently answered single-choice | **M** |
| 5 | Show more than 600 characters | You approve a truncated command | **S** |
| 5 | Approve-with-edit | Free text can only deny today | **M** |

## Tier 1 — real gaps, cheap to close

### Approver identity (`TG_ALLOWED_USERS`)

`TelegramChannel._on_update` checks the *chat* a button press came from and
never looks at `callback_query["from"]["id"]`. In a group with several people,
every member can approve a tool call running on your machine. The bridge
authenticates the room, not the person.

An allowlist compared against the presser's user id, empty meaning "anyone in
the chat" so the default is unchanged:

```python
ALLOWED = {int(u) for u in os.environ.get("TG_ALLOWED_USERS", "").split(",") if u}
if ALLOWED and cq["from"]["id"] not in ALLOWED:
    await self.call("answerCallbackQuery", callback_query_id=cq["id"],
                    text="Not authorised to answer for this session")
    return
```

Roughly fifteen lines, and it is the honest answer to "how do I secure the
group" — membership is too coarse a grain for an approval authority. The same
check belongs on free-text replies.

### Auto-allow rules

There is no rules engine, so every tool call becomes a notification. A session
that reads thirty files sends thirty prompts. This is the most likely reason
someone stops using the bridge after a day.

```
BRIDGE_AUTO_ALLOW=Read,Grep,Glob
BRIDGE_AUTO_ALLOW_BASH=pytest*,git status,git diff*,ls*
BRIDGE_AUTO_DENY_BASH=rm -rf /*,git push --force*
```

Matched before `chan.ask`, with auto-decided calls still recorded in the store
so the admin page shows them without anyone's phone buzzing. This is what turns
the bridge from "approve everything" into "approve what matters".

## Tier 2 — high value, more work

### Context: why is Claude asking?

A prompt today reads `🔐 Bash wants permission` over `rm -rf ./playground/scratch`
and says nothing about why. On a phone, away from the terminal, that is often
too little to decide on, and denying out of uncertainty makes the tool tiring.

Every hook payload carries `transcript_path`, which the bridge never opens.
Two options, cheapest first:

- Add the `UserPromptSubmit` hook, so each topic opens with *"you asked: fix
  the failing auth tests"* and every later permission inherits that context.
  One extra message per turn.
- Tail `transcript_path` for the last assistant message before the tool call.
  More precise, more parsing.

### Recovering a timed-out prompt

When `BRIDGE_TIMEOUT` expires the bridge returns `{}` and control falls back to
the terminal — but if nobody is at the terminal, the tool call now waits on a
prompt no one will see, and answering late on the phone does nothing.

Worth adding a **+5 min** button that extends the wait, and a reminder ping at
around 80% of the timeout. Both have to stay under the hook `timeout` in
`settings.json`, which remains the hard ceiling.

### Coalescing bursts

Five permissions in three seconds should arrive as one message with five rows.
A short debounce in `ask`, keyed by session. The difference between a usable
and a hostile notification pattern.

## Tier 3 — sending a new prompt into a session

The bridge is strictly reactive: it can answer questions, never ask them.
Of the two approaches, `claude -p --resume <session_id> "<text>"` is the
better one — `session_id` is already in every hook payload and in the store,
whereas `tmux send-keys` depends on how the user happened to launch Claude
Code and breaks when they did not use tmux.

The honest caveat: `--resume` runs a separate headless turn rather than typing
into the live interactive session, so a reply forks the conversation instead of
continuing it. That may still be what people want — "run the tests again",
"what is the status" — but it should be presented as a headless side channel,
not as remote typing.

## Tier 4 — operational

- **`systemd --user` unit and a Dockerfile** in `contrib/`, with
  `EnvironmentFile` at mode `0600`. Removes the "how do I keep this running"
  question.
- **Metrics on the admin page.** The store already records `duration_ms` and
  outcomes; allow/deny ratio, median response time and timed-out count are a
  few lines of aggregation over data that is already there — and they tell you
  whether the auto-allow rules are tuned correctly.
- **Webhook mode** would lift the one-poller-per-token limit, but needs public
  inbound and therefore a tunnel, the same requirement as the Twilio channel.
  Not worth it until someone asks.

## Tier 5 — polish

| Now | Better |
|---|---|
| `_finalize` posts a second message per answer | Edit the original message instead — halves chat volume |
| Only `/ping` and `/pending` | Add `/sessions`, `/mute`, and `/deny_all` as a panic button |
| Every event notifies equally | `disable_notification` on `Stop` and `Notification`; ring only for permissions |
| `multiSelect` answered single-choice | Toggle-style buttons with a Done row |
| Tool input truncated at 600 characters | A **show more** button, or the remainder on the admin page |
| Free text can only deny | Approve-with-edit — `PreToolUse` accepts `updatedInput` |

## Deliberately not planned

- **Persisting the admin history.** The events carry commands, file paths and
  prompts; keeping them in memory only is a decision, not an omission. See
  `store.py`.
- **Mutual TLS.** Claude Code's HTTP hooks send no client certificate, so the
  bearer token is the client's identity. An mTLS reverse proxy in front of a
  loopback bridge is the answer, and the README says so.
- **A second chat channel before Tier 1 and 2 land.** The `Channel`
  abstraction is the right shape, but breadth across transports is worth less
  than depth on the one that already works.

## If only three things get done

1. **Approver allowlist** — closes a live security gap, about fifteen lines
2. **Auto-allow rules** — the difference between usable and abandoned
3. **Context from `UserPromptSubmit`** — makes a remote decision an informed one

Together perhaps a day's work, and they change the product more than everything
below them combined.
