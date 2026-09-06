# Setting up the Telegram side

The bot takes a minute. The group is where people get stuck, and there are
exactly two traps.

If `bridge-for-agent-test/` is present, `bin/tg-setup.sh` does all of this
except the Telegram-app steps: it validates the token with `getMe`, clears any
webhook, lists the chats the bot can see, prefers a supergroup, writes a `.env`
at mode `0600` and sends a test message. Prefer it.

## The bot

`@BotFather` → `/newbot` → copy the token. It belongs in a shell profile or a
`0600` file — never a settings file, never git.

## The group

Direct chat with the bot works and needs none of this; you lose per-session
topics. For topics:

1. **New Group** in the mobile or desktop app. Not Telegram Web — it has no
   Topics toggle. Add any contact; Telegram will not create an empty group.
2. Group name → **Edit** → toggle **Topics** → Save. This converts it to a
   supergroup. There is no longer a member minimum.
3. Add the bot: `https://t.me/<yourbot>` → ⋮ → **Add to Group or Channel**. A
   bot cannot join through an invite link.
4. Group name → **Administrators** → **Add Admin** → the bot → enable
   **Manage Topics**.
5. Post `/start@<yourbot>` in General.

## Trap one: privacy mode

Bots run with privacy mode **on**. They receive only commands, @mentions, and
replies to their own messages — a plain group message never reaches them. A
bare `/start` is not routed to a specific bot either; the `@<yourbot>` suffix
is what does it.

Until something reaches the bot, `getUpdates` reports no chats and there is no
way to discover the chat id. That is why step 5 exists.

```bash
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/getMe" | grep -o '"can_read_all_group_messages":[a-z]*'
```

`false` is privacy mode on, and that is the right setting. The bridge only
needs replies to its own messages; turning it off would let the bot read the
whole group for no gain.

## Trap two: Manage Topics

Adding the bot is not enough. Without the **Manage Topics** admin right,
`createForumTopic` returns `400 not enough rights to create a topic` and the
bridge falls back to posting everything in General — working, but with the
feature silently absent.

```bash
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/getChatMember?chat_id=$TG_CHAT_ID&user_id=${TG_BOT_TOKEN%%:*}"
```

Want `"status":"administrator"` and `"can_manage_topics":true`.

## Finding the chat id

Post something the bot can see, then:

```bash
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/getUpdates"
```

A supergroup id is negative and starts `-100`. A direct chat id is positive.

**Stop the bridge before running this.** `getUpdates` is exclusive and
consuming: a running bridge will have taken the message already and discarded
it as not-for-me.

## Hooking up Claude Code

Merge `examples/hooks.settings.json` into `~/.claude/settings.json`, or a
project's `.claude/settings.json`. `/hooks` confirms they loaded. The hook
`timeout` must exceed `BRIDGE_TIMEOUT` so the bridge always answers first.
