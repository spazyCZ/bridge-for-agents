---
name: notify-user
description: Send the user a push notification on their phone via bridge-for-agents. Use when reporting progress from a long-running task or /loop, when a job finishes, when something needs attention while the user is away from the terminal, or whenever the user asks to be told, pinged, updated or notified about something. Also covers what must never be put in a notification.
---

# Notifying the user

`notify_user` puts a line on the user's phone. They are not at the terminal —
that is the whole reason to use it.

**It is one-way.** They cannot reply to it, and you get back nothing but
confirmation it was sent. If you need an answer, use the client's normal
question tool. The bridge routes Claude Code's `AskUserQuestion` to the same
phone with buttons; Codex questions currently stay in the Codex UI.

## Never send

The message leaves the machine and is stored by a third-party chat service.
Before sending, check the text for:

| Never | Instead |
|---|---|
| API keys, tokens, passwords, connection strings | "the deploy key is missing from the environment" |
| Contents of `.env`, `secrets.*`, `id_rsa`, credential files | name the file, never a line of it |
| Full file contents or diffs | "3 files changed in `src/auth/`" |
| Stack traces | the exception type and where — "`KeyError: user_id` in `auth.py:42`" |
| Customer data, emails, names, anything personal | a count — "14 rows failed validation" |
| Absolute paths revealing usernames or clients | a repo-relative path |
| Full command lines that embed credentials | the command's *purpose* |

The bridge redacts recognisable credentials as a backstop and tells the user
how many it hid. **Do not rely on it.** It knows common key shapes; it does not
know your customer's phone number, your internal hostnames, or a token in a
format it has never seen. Getting it right is your job; redaction is the safety
net under it.

If you are unsure whether something is sensitive, leave it out and say where to
look: *"auth tests failing — see the terminal"* is a good notification.

## Write it for a lock screen

A notification is read in two seconds, one-handed, out of context. Lead with
the outcome.

**Good**

```
Deploy to staging finished. 47 tests passed, 2 skipped.
Migration 0034 blocked: column "email" already exists. Rolled back, nothing lost.
Still running — 3 of 12 packages built, ~8 min left.
Done with the refactor. 18 files changed, tests green. Needs review before merge.
```

**Bad**

```
✅✅ UPDATE ✅✅                          (says nothing)
Task completed successfully.             (which task? what happened?)
Error occurred                           (which error? does it matter now?)
Here is a summary of the work I did: ... (three paragraphs on a lock screen)
Running `psql -h prod-db-3 -U admin ...` (a hostname and a username, needlessly)
```

Rules of thumb:

- One or two lines. Anything longer belongs in the terminal.
- Say what happened, then what it means. Numbers over adjectives.
- No preamble. Not "I wanted to let you know that…", just the fact.
- At most one emoji, and only if it adds meaning. The bridge already prefixes
  a level icon.
- Plain text. Markdown is not rendered.

## Levels

- `info` — progress and completion. The default.
- `warn` — something is off but the work continues. A retry, a skipped step,
  a slower path.
- `error` — the work stopped and will not finish without them.

Reserve `error` for things worth interrupting someone for. A notification that
cries wolf gets muted, and then the real one is missed too.

## In a loop

The common case: `/loop` reporting while the user is away.

- **Only notify when something changed.** A loop that sends "still running"
  every five minutes trains the user to ignore it. If the state is the same as
  last time, stay quiet.
- **Always notify on a state change**: finished, blocked, failed, or needing a
  decision.
- **Include position** so the message stands alone: "4 of 12 done", not "next
  one done".
- **Once at the end**, with the outcome. That is often the only one they read.

A good loop sends two or three notifications an hour, not twenty. The bridge
enforces a ceiling (`BRIDGE_NOTIFY_RATE`, 20/min by default) but that is a
runaway guard, not a target.

## If it fails

The tool returns an error when the bridge is unreachable or refuses the
message. **A failed notification is not a failed task.** Say so in the terminal
and carry on with the work — do not retry in a tight loop, and do not stop what
you were doing because the user could not be told about it.
