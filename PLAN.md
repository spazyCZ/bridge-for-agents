# claude-bridge — plan

> **Status** — maintained alongside the plan below, which is kept as written.
>
> | Phase | State |
> |---|---|
> | 0 — Strip | **done**, except one item that did not apply (see note) |
> | 1 — Enforce one-way | **done**, with exit test in `tests/test_invariant.py` |
> | 2 — Audit log | **core done** — log, chain, verifier. Anchor, rotation and rich filters deferred |
> | 3 — Deployment hardening | not started |
> | — Notifications | **done** — `notify_user` MCP tool, send-only, rate-limited, redacted, audited |
> | 4 — Verify against reality | **not started, and sequenced second** |
> | 5 — Second channel | deferred, possibly indefinitely |
>
> **Phase 0's keyfile item did not apply to this repository.** There is no
> `Account`, no `load_accounts`, no `keys.example.json` here; this codebase has
> always been single-`BRIDGE_TOKEN`, single-`TG_CHAT_ID`. That bullet was
> written against a different variant. Nothing was deleted for it.
>
> Phase 1 turned up a real defect while being tested: `_drop` checked the chat
> id on the message path but not on the callback path, so a callback from any
> chat was recorded *and* answered. Fixed; `test_updates_from_another_chat_are_ignored_entirely`
> covers it.
>
> **Phase 2 was built to the core and stopped there, deliberately.** Shipped:
> the append-only JSONL at `0600` with `fsync`, sequence and chain continuity
> across restarts, the six record types, full unredacted tool input, the
> `button`/`text`/`timeout`/`terminal` source distinction, the optional HMAC
> chain, and `bridge-audit-verify`. Deferred: the hourly Telegram anchor, size
> rotation with chain continuity, and the verifier's `--since` / `--tool` /
> `--outcome` filters. Nothing deferred is blocked — they are additive.
>
> This plan supersedes [ROADMAP.md](ROADMAP.md) where the two disagree.

Reorientation from "remote control over Telegram" to a **single-user, one-way
approval channel with a provable record**. Security and audit are the product;
convenience features are only kept when they don't weaken either.

## The invariant

> Every message to the phone originates **on the Claude Code host** — from a
> hook, or from a tool the agent called there.
> Nothing the chat sends can start anything — it can only answer a request
> that is already open and waiting.

**The first clause was widened, deliberately and once.** It originally read
"originates from a Claude Code hook". Adding `notify_user` — an MCP tool the
agent calls to push a progress line to the phone — meant a message could also
originate from a tool call. The wording now says what actually holds.

The security-relevant half is untouched. `notify_user` is **send-only**: it
posts to `/notify`, which calls `send` and never `ask`, awaits nothing, and
returns nothing but confirmation. No chat content can reach the agent through
it. A tool that could read a reply would break the invariant, and is not going
to be added.

If a future change makes the second clause harder to state than it is here, it
is the change that is wrong.

Everything below either enforces this, records it, or gets cut for conflicting
with it. If a future feature can't be added without breaking it, it doesn't
get added.

Explicit non-goals: injecting prompts into a session, starting sessions,
`--resume` from chat, running commands, querying state. These are what the
existing tools (ghost-code, the PyPI bridge) exist to do. Not building them is
the point, and it's why forking one of those is the wrong move here.

---

## Phase 0 — Strip (half a day)

Remove what single-user makes dead weight. Less code is a security property.

- Delete the keyfile machinery: `Account`, `load_accounts`, `keys.example.json`.
  Back to one `BRIDGE_TOKEN` and one `TG_CHAT_ID`.
- Delete `/ping` and `/pending`. They're chat-initiated actions. Harmless
  today, but they're the precedent that makes the invariant negotiable.
- Keep forum topics. Their job changes from isolation to **audit legibility** —
  one thread per session is what makes the record readable months later.
- Keep TLS optional, keep the HTTP default.

Exit: `bridge.py` is smaller than it is now and does strictly less.

## Phase 1 — Enforce one-way (half a day)

Make the invariant checkable by reading one function rather than inferring it.

- Rewrite `_on_update` as a single guard: resolve the `rid` for this
  (chat, message) pair; if there is no *pending* request for it, drop the
  update and audit it as `rejected_unsolicited`. No other branch exists.
- Same for callbacks: the `rid` must match a live pending entry in this chat.
- Audit every dropped update. An unsolicited message is a signal, not noise —
  it means someone with access to the chat tried to initiate something.
- Add a module docstring stating the invariant, so a future reader knows the
  omissions are deliberate.

Exit: a test that posts a message and a stale callback with no request open,
and asserts nothing happens beyond an audit record.

## Phase 2 — Audit log (2–3 days, the real work)

### Format

Append-only JSONL at `BRIDGE_AUDIT` (default
`~/.local/state/claude-bridge/audit.jsonl`), file mode `0600`,
`fsync` on every write. One record per event:

```json
{
  "seq": 41,
  "ts": "2026-09-05T14:22:31.104Z",
  "type": "request_open",
  "session_id": "a1b2c3d4…",
  "cwd": "/home/roman/repo",
  "event": "PermissionRequest",
  "tool": "Bash",
  "input": {"command": "git push --force origin main"},
  "prev": "9f2c…",
  "mac": "4b81…"
}
```

Record types:

| type | when | carries |
|---|---|---|
| `bridge_start` | startup | version, config fingerprint (not the secrets) |
| `request_open` | hook received | session, cwd, event, tool, **full tool input** |
| `decision` | answer or timeout | outcome, source, latency_ms, `ref` → seq of the request |
| `rejected_unsolicited` | phase-1 drop | chat id, message id, text (truncated) |
| `auth_failure` | bad/missing key | source address, path |
| `session_end` | SessionEnd hook | session id |

`source` on a decision is one of `button`, `text`, `timeout`, `terminal`
(bridge returned `{}` and Claude Code prompted locally) — this distinction is
the whole point: it separates *what you approved* from *what the bridge
declined to decide*.

Store the full tool input, not the truncated summary that goes to Telegram.
The phone message is a UI; the audit record is evidence.

### Tamper-evidence

Chain each record: `mac = HMAC-SHA256(audit_key, prev || canonical_json(record))`,
where `canonical_json` is sorted-keys, no whitespace. `prev` is the previous
record's `mac`; genesis uses zeros.

- `audit_key` comes from `BRIDGE_AUDIT_KEY` and must **not** live next to the
  log. Different file, different mode, ideally a different owner.
- This detects edited or deleted lines by anyone without the key. It does not
  stop someone who holds both key and file from rewriting the whole chain —
  no local-only scheme can. Be honest about that in the README rather than
  implying more.

### External anchor

Once an hour (and on shutdown), post the current head `mac` and `seq` to the
Telegram topic as a plain message. Telegram then holds an independent,
timestamped copy of the chain state that a local attacker can't retroactively
change. Cheap, and it closes the gap the previous paragraph admits to.

Pin the daily anchor so it's findable.

### Verifier

`audit-verify.py`:

- recompute the chain from genesis, report the first broken link with its seq
- cross-check heads against the anchors pasted back in from Telegram
- `--session <id>` to print one session's full request/decision history
- `--since`, `--tool`, `--outcome deny` filters for review
- exit non-zero on any break, so it can run from cron or CI

Exit: a test that flips one byte in the middle of the log and asserts the
verifier names the right seq.

### Retention

Rotate at a size threshold; the new file's genesis `prev` is the old file's
head `mac`, so the chain survives rotation. Never delete without recording it.

## Phase 3 — Deployment hardening (1 day)

- `systemd` unit: `DynamicUser` or a dedicated `claude-bridge` user,
  `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` limited to the
  state dir, `EnvironmentFile` mode `0600` for the bot token and keys.
- Log the config fingerprint at startup (which options are on) so the audit
  shows the posture the bridge was running under.
- Document the trust boundary plainly: anyone who can POST a valid key can make
  your phone buzz with an approval prompt; anyone with the Telegram account can
  answer one. The audit log is what makes both detectable after the fact.
- Firewall rule limiting the port to the Claude Code host.

## Phase 4 — Verify against reality (ongoing, start now)

The one genuinely unverified thing: the `updatedInput.answers` shape for
`AskUserQuestion`. Read how `claude-watch` and `ghost-code` handle it — both
have working implementations — then confirm against a live session with the
bridge log open. If the shape is wrong, return `{}` and fall back to the
terminal rather than guessing; a wrong answer silently accepted is worse than
no answer.

Also worth a real run: multi-hour session, sleep/resume on the bridge host,
Telegram rate limits when a session asks rapidly.

## Phase 5 — Second channel (later, only if wanted)

The `Channel` interface already isolates this. WhatsApp via Twilio needs a
public inbound webhook, which means a tunnel and a signature check — a
meaningfully larger attack surface than outbound-only polling. Given that
security is the point here, treat it as a deliberate tradeoff, not a natural
next step. Telegram-only may simply be the right answer.

---

## Sequence

1. Phase 0 + 1 together — they're small and they define the shape.
2. Phase 4's payload check — do it early, it can invalidate assumptions.
3. Phase 2 — the substantial work, and the differentiator.
4. Phase 3 before any real use.

## Open questions

- Should a `deny` carry your typed reason into the audit record *and* back to
  Claude (it does today)? That text becomes model input — worth deciding
  consciously rather than by default.
- Timeout default of 540 s: too short for a phone in a pocket, too long for a
  blocked CI job. May need to differ by event type.
- Does the audit log belong on the bridge host at all, or should records ship
  to an append-only store elsewhere as they're written?
