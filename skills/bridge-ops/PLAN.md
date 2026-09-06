# Plan: a `bridge-ops` skill

A skill so Claude Code can set up, run and diagnose the bridge — and behave
sensibly while the bridge is gating it.

Not built yet. This is the design and the reasoning, so that building it is a
short job and an argued one.

## Why a skill rather than the docs we already have

1,466 lines across six files already describe all of this. An agent asked *"the
bridge stopped working"* would have to guess which to read, and would read
prose written for a person sitting down with it once.

A skill earns its place by adding four things the docs cannot:

- **It arrives unasked.** The moment worth helping is when someone says "no
  prompts are reaching my phone", not when they think to open the guide.
- **Decision procedures, not prose.** Symptom → the one command that
  distinguishes two causes → the fix. Ordered, so the cheap check comes first.
- **Hazards the docs state calmly and an agent will walk into.** See below.
  This is the strongest argument for the skill existing.
- **Knowing what not to do.** Several reasonable-looking actions are wrong
  here, and only a skill is present at the moment the agent is about to take
  one.

If the skill turns out to be USER-GUIDE.md rearranged, it should not ship.

## The hazards it exists to prevent

These are specific to this project, non-obvious, and an agent will hit them.
Getting these right matters more than the coverage.

**The audit log holds unredacted secrets, on purpose.** Everything else in the
system redacts. `audit.jsonl` deliberately keeps the full tool input, because
it is evidence. An agent diagnosing a problem will reach for `cat`, `tail` or
`grep` on it and put an API key straight into the transcript. The skill must
teach reading it through `bridge-audit-verify --session <id>`, and treat
dumping it as the mistake it is.

**Restarting the bridge cuts the channel that approves the restart.** If the
agent is being gated by the bridge, `pkill` needs approval, and once the bridge
is down no further approval can arrive — including for the command that starts
it again. The skill needs the sequence that works, and needs to say plainly
when to hand back to the user instead.

**One token, one process.** An agent that "restarts" by starting a second
instance produces the failure that is wrong rather than broken: both pollers
take updates at random. Always stop before starting, and check.

**Config diagnosis wants to print the config.** `cat .env`, `env | grep TG`,
echoing `$TG_BOT_TOKEN` — each is the natural next step and each leaks a
credential into the transcript. The skill needs the checks that answer the
question without printing the value: `getMe` for validity, the startup card for
identity, `${TOKEN%%:*}` for the bot id.

**The invariant is a decision, not an omission.** An agent asked to "make the
bridge more useful" may add a chat command or a tool that reads replies. The
skill must carry the invariant and the reason, so the agent argues back instead
of helpfully breaking it.

## Scope

**In:** installing and configuring; starting, stopping and restarting; reading
the admin page, the diagnostic log and the audit log; diagnosing the common
failures; token rotation; the deployment topologies; what to expect while a
prompt is pending.

**Out:** writing notifications — `notify-user` already covers that, and it
triggers on a completely different moment ("tell the user X" against "the
bridge is broken"). Two skills, not one. Also out: developing the bridge,
which is CONTRIBUTING's job.

## Shape

```
skills/bridge-ops/
├── SKILL.md              # triggers, hazards, and the diagnostic procedure
└── references/
    ├── setup.md          # BotFather, the group, topics, privacy mode
    ├── diagnose.md       # symptom → distinguishing check → fix
    └── configure.md      # env vars, topologies, systemd, rotation
```

SKILL.md stays under ~250 lines: the hazards in full, a first-response
procedure, and clear pointers to which reference answers which question. The
references carry the detail so the common case costs little context.

`references/diagnose.md` is the piece worth most care. One row per symptom:
what you see, the single command that separates the likely causes, and what
each answer means. The three logs each answer a different question — the admin
page what the bridge thinks is happening, the diagnostic log what it tried, the
audit log what was actually decided — and the skill should say which to open
first for each symptom rather than listing all three every time.

## Triggering

The description is the whole mechanism, and the risk here is under-triggering:
someone says "I'm not getting prompts on my phone" without naming the bridge at
all. It has to cover the symptom vocabulary, not just the product name.

Draft:

> Set up, run and diagnose the bridge-for-agents Telegram approval daemon. Use
> whenever prompts are not reaching the phone, the bridge will not start or has
> stopped, topics are not being created, a bot token needs rotating, or someone
> is configuring Telegram approvals for Claude Code — and whenever a question
> is about the bridge's admin page, audit log or hook configuration, even if
> the bridge is not named.

To be optimised against real queries rather than guessed at, using
skill-creator's description loop.

## How it gets verified

Two things need testing, and they fail differently.

**Does it trigger?** ~20 queries, half should-trigger and half not. The valuable
negatives are near-misses, and the sharpest is `notify-user`: *"let me know when
the tests finish"* must go there, not here. Others worth including: a generic
Telegram bot question with no bridge involved; a hooks question that is really
about Claude Code configuration; "my notifications aren't working" where the
user means macOS notifications.

**Does it help?** A handful of realistic scenarios run with and without the
skill — a broken setup where the bot is not an admin, an empty phone where the
bridge is not running, a request to rotate the token. Judged on: did it find
the cause, did it avoid the hazards above, and did it get there in fewer steps.
The hazard checks are the objective ones — did the transcript end up containing
a secret, did it start a second bridge — and those are worth asserting rather
than eyeballing.

## Sequencing

Worth writing **after** two things land, because both change what operating the
bridge means:

- **Phase 4**, the `AskUserQuestion` payload check. If the shape is wrong the
  answer path changes, and the skill would be teaching a broken one.
- **Phase 3**, the systemd unit. Once there is a unit, "restart the bridge" is
  `systemctl --user restart bridge` rather than `pkill` and a manual start —
  and the restart hazard above largely dissolves. Writing the skill first means
  writing the awkward version and then rewriting it.

Until then the material keeps shifting. This document is the placeholder.

## Open questions

- **Does the skill assume the test project?** `bridge-for-agent-test` has
  `bin/up.sh`, `tg-setup.sh` and `rotate-token.sh`, which turn several
  procedures into one command. Depending on them makes the skill much shorter
  and much less portable. Probably: prefer them when present, describe the
  manual path otherwise.
- **Shipped where?** In-repo under `skills/` matches `notify-user` and is
  versioned with the code it describes, but it only takes effect once copied to
  `~/.claude/skills/`. A plugin would install properly; that is a bigger change.
- **Should it be able to act, or only advise?** Restarting a daemon and
  rotating a credential are not obviously things an agent should do
  unsupervised. Leaning towards: diagnose freely, and hand the destructive
  steps back with the exact command to run.
