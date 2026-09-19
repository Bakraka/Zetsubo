# Sins Bot 2.0 Setup Guide

## Installation

```bash
python3 -m pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 seven_sins_bot_v2.py
```

Do not put the token in the Python file. The bot reads `DISCORD_TOKEN` from the
environment.

## Discord permissions

Enable these privileged intents:

- Server Members Intent
- Message Content Intent

The bot needs:

- Manage Roles
- Manage Channels
- Moderate Members
- View Channels
- Send Messages
- Embed Links
- Read Message History
- Add Reactions
- Manage Messages

Move the bot's role above all roles managed by this bot. If a role is above
the bot, Discord will reject both assignment and removal.

## Initial setup

Run:

```text
!setup
```

`!setup` creates the managed roles and repairs duplicate ownership. Sins Bot
2.0 does not create trial channels, trial roles, combat channels, or
power-up channels.

## Managed roles

| Key | Discord role | Power |
|---|---|---:|
| `lust` | Desire Bound Lust | 1 |
| `gluttony` | The Devoured | 2 |
| `greed` | The False King | 3 |
| `sloth` | The vessel of sloth | 4 |
| `wrath` | Crimson heir | 5 |
| `envy` | The Pale Mirror | 6 |
| `pride` | The bearer of pride | 7 |
| `chastity` | The Chaste | 8 |
| `temperance` | The Fasting King | 9 |
| `charity` | The Open Hand | 10 |
| `diligence` | The Waking | 11 |
| `patience` | The Still Flame | 12 |
| `kindness` | The Mirror's Grace | 13 |
| `humility` | The Humble Sovereign | 14 |
| `justice` | The Scales of Justice | 15 |
| `prudence` | The Prudent Eye | 16 |
| `fortitude` | The Unbroken | 17 |
| `faith` | The Faithful | 18 |
| `hope` | The Hopeful | 19 |
| `liberality` | The Open Spirit | 20 |
| `despair` | The Ultimate Despair | 21 |
| `izuru_despair` | Izuru Kamakura: Remnant of Despair | 22 |
| `izuru_hope` | Izuru Kamakura: Ultimate Hope | 23 |

Only one member can hold each managed role. The bot keeps a persistent
`role_holders` ledger and also watches Discord role changes.

## Role requests

### Request a role

```text
!request_role <role-key>
```

Examples:

```text
!request_role lust
!request_role justice
!request_role The Scales of Justice
```

Role keys are case-insensitive. `!roles` shows every key and current holder.

Roles below power 5 are granted immediately when available. Power 5 and above
create an approval request. The required approvals are:

```text
required = max(2, min(6, power - 5 + 1))
```

### Approve a request

```text
!approve_role @requester <role-key>
!role_requests
```

The requester cannot approve their own request. Each approver counts once. The
role is assigned automatically when the threshold is reached.

### Owner override

```text
!owner_grant_role @member <role-key>
```

Only the Discord server owner can use this command. It bypasses the approval
threshold but still verifies that Discord actually assigned the role.

The server owner can strip their own manageable roles with:

```text
!owner_strip
!owner_strip_all
```

This keeps `@everyone`, skips managed roles and roles above the bot, and clears
the ownership ledger for any managed roles that were removed.

### Release and repair

```text
!release_role <role-key>
!myroles
!sync_roles
!repair_roles
```

The role holder can release their own role. The server owner can release any
managed role. Administrators can run `!sync_roles` after manual Discord role
changes.

## Owner personal strip

The normal `!strip_all` command from the old bot intentionally protected the
server owner. In 2.0 the owner has a separate personal command:

```text
!owner_strip
!owner_strip_all
```

It affects only the owner who invokes it, retains `@everyone`, and skips
managed roles or roles above the bot's highest role.

## Ability commands

There are no HP or damage abilities in 2.0. Effects are target-scoped and use
one of these outcomes:

- longer cooldowns
- slower typing recovery
- lower proc chance
- temporary ability locks
- Discord timeouts
- short mutes
- temporary removal from the current text channel
- clearing negative effects

### Lust

```text
!obsess @user
!obsession_clash @user
```

### Gluttony

```text
!gorge
!feast @user
!devour @user
!purge_bite @user
!expel @user
```

### Greed

```text
!steal_ability @user
!i_always_get_what_i_want @user
!frenzy_clash @user
```

### Sloth

```text
!force_lazy @user
!slowdown @user
!force_sleep @user
!deep_sleep @user
!sleepwalker
```

### Wrath

```text
!rage_strike @user
!bloodlust
```

### Envy

```text
!jealousy_mark @user
!envy_check
!schizo @user
```

### Pride

```text
!weaken @user
!claim @user
!stop_time @user
```

### Virtues and special roles

```text
!abstain
!purify @user
!fast @user
!moderate @user
!gift_power @user
!return_ability @user
!inspire @user
!rouse @user
!de_escalate @user
!absorb_strike @user
!bless @user
!forgive @user
!submit
!counter_claim @user
!discern @user
!wise_counsel @user
!verdict @user
!anticipate
!endure
!fortify @user
!invoke_faith @user
!prayer @user
!rally @user
!beacon
!grant_freedom @user
!bestow @user
!redistribution @user
!break_chains @user
!condemn @user
!despair_wave @user
!divine_retribution @user
!holy_judgment @user
!inspire_strike @user
```

## Former path moves

Path selection was removed. These moves are now available in the base
moveset for anyone who holds a managed role:

```text
!support_ability @user
!attack_ability @user
!hybrid_ability @user
!tacht_strike @user
!tacht_burst @user
!reverence_aura
!demand_tribute @user
```

Despite the legacy name `!attack_ability`, it does not deal HP damage in 2.0;
it applies a proc penalty.

## Private ability buttons

```text
!abilities
/myabilities
!ability_list
```

`/myabilities` is an application command. It sends an ephemeral embed listing
the caller's managed roles and abilities. Clicking a button returns the exact
command syntax privately and never executes the ability.

The bot syncs application commands at startup. If `/myabilities` does not
appear, re-invite the bot with the `applications.commands` OAuth scope and
restart it.

## Target-effect safety

Every temporary effect is stored under the target's user ID. Discord timeouts
are applied directly to the mentioned target. Temporary channel removal saves
and restores that target's own channel overwrite. There is no shared global
mute flag, so one target's effect cannot accidentally mute another member.

## Runtime data

`sins_v2_data.json` contains role ownership, requests, per-user cooldowns, and
per-user effects. It is ignored by the included `.gitignore` and should not be
committed.