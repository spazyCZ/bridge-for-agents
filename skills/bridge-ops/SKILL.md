---
name: bridge-ops
description: Set up, run and diagnose bridge-for-agents, the daemon that routes Claude Code and Codex permission prompts to Telegram, plus Claude Code questions — and behave well while it is gating you. Use whenever prompts are not reaching the phone, the bridge will not start or has stopped, forum topics are not being created, a bot token needs rotating, or someone is configuring Telegram approvals for either client. Also use when a question is about the bridge's admin page, audit log, hook configuration, or why a command was denied or is waiting for approval — even when the bridge is not named, for example "I'm not getting prompts on my phone" or "why did it refuse that command".
---

# Operating the bridge

The bridge turns Claude Code and Codex permission prompts into Telegram
messages and turns the answers back into decisions. It runs as a daemon on the
same machine as the agent client, listening on `127.0.0.1:8765` by default.

Read the hazards first. They are short, they are specific to this system, and
each is something reasonable that goes wrong here.

## Five things not to do

**Do not print the audit log.** `audit.jsonl` deliberately holds the *full*
tool input, secrets and all, because it is evidence — everything else in the
system redacts, and this one does not. `cat`, `tail` or `grep` on it puts a
live credential into the transcript, into scrollback, and into anywhere the
transcript is later pasted. Read it through the verifier, which strips the
chain noise and lets you scope to one session:

```bash
bridge-audit-verify --session a1b2      # one session's history
bridge-audit-verify                     # is the chain intact
```

When you must look directly, select fields rather than dumping lines:

```bash
python3 -c "import json;[print(json.loads(l)['type'], json.loads(l).get('outcome','')) for l in open('audit.jsonl')]"
```

**Do not print the configuration.** `cat .env`, `env | grep TG`, echoing
`$TG_BOT_TOKEN` — each is the obvious next step when diagnosing, and each leaks
the token. Everything you actually need can be had without printing it:

```bash
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/getMe"   # valid? which bot?
echo "${TG_BOT_TOKEN%%:*}"                                   # the bot id alone
```

The startup card in the group's General topic already names the host, bot,
chat and scope — read that before asking the shell anything.

**Do not restart the bridge while it is gating you.** If your own tool calls
are being approved through it, stopping it removes the channel that would
approve starting it again, and the session wedges. Before any restart, say so
and let the user decide. If they want it done anyway, the safe order is: warn,
stop, start, and confirm the new startup card appeared — and if the stop needs
approval, get that approval *first*, while the bridge still works.

**Never start a second bridge on the same token.** `getUpdates` is exclusive:
two pollers take updates at random, so an answer lands on whichever polled
first. This is wrong rather than broken, and it is silent apart from a one-time
warning in the chat. Always stop before starting:

```bash
ps -eo pid,comm,args --no-headers | awk '$2 ~ /python/ && /-m bridge_for_agents/'
```

Matching on the *command name* being python matters: `pgrep -f` also
matches shell wrappers whose command line happens to contain the string, and
will report processes that are not there.

**Do not add anything the chat can initiate.** The bridge answers; it never
takes instructions from Telegram. No chat commands, no tool that reads replies,
no resume-from-chat. These are refused features, not missing ones — see
`PLAN.md` in the repository for the invariant and why it is worth keeping. If
asked to add one, say what it would cost before doing it.

## When something is wrong

Three sources answer three different questions. Choosing the right one first
saves most of the work:

| Question | Look at |
|---|---|
| What does the bridge think is happening? | the admin page, `http://127.0.0.1:8765/admin` |
| What did it try to do? | the diagnostic log (stderr, or `BRIDGE_LOG_FILE`) |
| What was actually decided, and when? | the audit log, via `bridge-audit-verify` |

Start here, in this order:

```bash
curl -s localhost:8765/health                      # is it even up
ps -eo pid,comm,args --no-headers | awk '$2 ~ /python/ && /-m bridge_for_agents/'   # one, or two
curl -s localhost:8765/admin/api/state | python3 -c "import json,sys;print(json.load(sys.stdin)['totals'])"
```

If the admin page shows events arriving but nothing reaches the phone, the
problem is between the bridge and Telegram. If it shows no events, the problem
is between the agent and the bridge — check `/hooks` in the client and that its
hook configuration points at the bridge's actual port.

`references/diagnose.md` has one row per symptom, with the single command that
separates the likely causes. Read it when the three checks above do not settle
it.

## Setting it up

`references/setup.md` — BotFather, the group, topics, and the two traps that
cost people an afternoon: **privacy mode**, so a plain group message never
reaches the bot and a bare `/start` is not routed to it, and the **Manage
Topics** admin right, without which topic creation fails and everything lands
in General.

If `bridge-for-agent-test/` is present alongside the repository, prefer its
scripts — `bin/tg-setup.sh` does token validation, chat discovery and writes a
`.env`; `bin/up.sh --real` starts things; `bin/rotate-token.sh` handles a
rotation. They are shorter and better tested than doing it by hand.

## Configuring it

`references/configure.md` — the environment variables, the three deployment
topologies and which to pick, and token rotation.

Two things worth knowing without opening it. **A bot token cannot be rotated
through an API**: only `@BotFather` can revoke one, so the human step is
unavoidable and the check that matters afterwards is whether the *old* token is
dead. And **on one machine there is nothing to configure**: the bridge binds
loopback, so no shared secret, no TLS and no certificates are needed.

## Using the bridge well

### What to tell the user

You are usually the one who set this up, so you are the one who should explain
living with it. Worth saying without being asked:

- **Deny is the safe answer.** A prompt they did not expect means something
  reached the bridge that they did not start. Denying costs a retry.
- **Typing beats tapping when there is a nuance.** `y` and `n` work, but so
  does *"no, use staging"* — the text becomes the reason, and the agent reads
  it. On a question, `2 - but check the migration first` picks option two and
  carries the caveat. That is how you redirect work from a phone.
- **Ignoring a prompt is a valid answer.** After `BRIDGE_TIMEOUT` it returns to
  the terminal and waits there. Silence approves nothing.
- **One topic per session** is how to follow two agents at once; closed topics
  are the finished ones.
- **The admin page** answers "what is it waiting for" without unlocking a
  phone.

Set expectations early. Someone who has just wired this up does not yet know
that reading thirty files will buzz them thirty times, or that a timed-out
prompt is not lost. Saying so up front saves the first bad afternoon.

### How to behave while it is gating you

**Write commands that can be approved.** What reaches the phone is one line,
truncated at 600 characters, with credentials redacted. A long shell pipeline
arrives as an unreadable blob, and the right response to an unreadable blob is
Deny. `pytest tests/auth -q` as its own step beats the same work folded into a
chain of five commands — not for tidiness, but because legible steps are the
ones that come back approved. When a command cannot read clearly in one line,
say what it does in the terminal first.

**A denial with a reason is an instruction.** The text is the user's own words,
typed on a phone: *"not on production"*, *"use the staging database"*. Running
the same command again is the wrong move. Adapt to what they said; if it is
ambiguous, ask rather than guess.

**Questions cost a notification and a wait.** `AskUserQuestion` reaches the
phone the same way. Three questions in a row is three buzzes and three waits.
Ask one question that carries the real decision, and only when the answer
changes what you do next.

**Prefer reversible work while they are away.** A prompt that times out falls
back to a terminal nobody is watching. Doing the reversible parts and reporting
what remains beats stalling on the first thing needing a decision.

**Notifying them** is a separate skill — see `notify-user`, which also covers
what must never go in a notification.

### Is the bridge even there?

Hooks in `settings.json` pointing at `/hook`, a `bridge-notify` MCP server, a
daemon answering on `127.0.0.1:8765`. If none of that is true, prompts are
appearing in the terminal as usual and none of this applies.

## Reference files

| File | Read it when |
|---|---|
| `references/diagnose.md` | something is broken and the three checks above did not settle it |
| `references/setup.md` | installing, or the Telegram side is misconfigured |
| `references/configure.md` | changing environment variables, planning a deployment, rotating a token |
