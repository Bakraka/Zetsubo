# Zetsubo — Seven Sins Bot 2.0

Sins Bot 2.0 is a smaller Discord role-and-ability system. It keeps the
roles, abilities, and effects while removing trials, combat, HP, evolutions,
second modes, and power-up gates.

## What changed in 2.0

- Members request an available role directly.
- Each managed role has exactly one holder at a time.
- Low-power roles are granted immediately when available.
- Higher-power roles require community approvals, or the server owner can
  grant them directly.
- Manually assigned Discord roles are reconciled into the ownership ledger.
- Role assignments are verified against Discord after every grant.
- Former path abilities are base moves and do not require a path selection.
- All abilities use cooldowns and status effects instead of HP damage.
- Effects are stored per target, preventing one user's mute or cooldown from
  affecting someone else.
- `/myabilities` privately lists the caller's abilities with clickable command
  hints.

## Removed from the old bot

- Trial windows and trial channels
- Evolved/second modes
- Combat and HP
- Damage-based moves
- Power-up meters and path unlock requirements
- Automatic trial/obtainment sequences

## Run locally

```bash
python3 -m pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 seven_sins_bot_v2.py
```

The `Procfile` starts the same entry point for Railway:

```text
worker: python3 seven_sins_bot_v2.py
```

Enable Server Members Intent and Message Content Intent in the Discord
Developer Portal. The bot needs Manage Roles, Moderate Members, Manage
Channels, Send Messages, Read Message History, and Add Reactions. Put the bot
role above every managed role.

## First setup

Run this as a server administrator:

```text
!setup
```

This creates the managed roles only. It does not create trial channels.

## Role flow

```text
!roles
!request_role lust
!request_role justice
!approve_role @member justice
!owner_grant_role @member justice
!release_role justice
!myroles
!sync_roles
```

Roles below power 5 are granted immediately if available. Roles at power 5 or
above create a request requiring approvals. The approval count scales with
power and is shown when the request is created. The server owner can bypass
approvals with `!owner_grant_role`.

## Ability viewer

```text
!abilities
/myabilities
!ability_list
```

`/myabilities` is private. Its buttons only show the command syntax; they do
not execute an ability or choose a target.

## Owner controls

```text
!owner_grant_role @member <role>
!owner_strip
!owner_strip_all
```

`!owner_strip` removes every manageable role from the server owner who invokes
it. It keeps `@everyone` and skips managed roles or roles above the bot.

## Data

Runtime state is stored in `sins_v2_data.json`. It contains role ownership,
pending approvals, cooldowns, and target-specific effects. Keep it private.