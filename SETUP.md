# Zetsubo — Setup and Command Guide

This guide matches the supplied `seven_sins_bot.py`. The bot uses the `!`
prefix and stores runtime state in `sins_data.json`.

## 1. Install and configure

```bash
python3 -m pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 seven_sins_bot.py
```

The bot requires `DISCORD_TOKEN` in the environment. It does not read a
hard-coded token from the Python file.

## 2. Discord Developer Portal

Enable:

- Server Members Intent
- Message Content Intent

The bot's invite permission integer is defined in the Python file as
`268528960`. The bot also needs these operational permissions:

- Manage Roles
- Manage Channels
- Moderate Members
- View Channels
- Send Messages
- Embed Links
- Read Message History
- Add Reactions
- Manage Messages
- Attach Files

The bot must be placed above the roles it needs to grant, remove, or manage.

## 3. Initial server setup

Invite the bot, then run this as a server administrator:

```text
!setup
```

`!setup` creates missing roles, categories, text channels, a banished voice
channel, and role-specific permission overwrites. It is safe to run again; the
bot reports what already exists.

### Main channels

The setup routine creates or aligns these shared channels:

- `#sins-tribunal`
- `#gluttony-feast`
- `#tutorial`
- `#banished-place`
- `#virtues-hall`
- `#myths`
- `#hope`

It also creates the trial channels `#lust`, `#gluttony`, `#greed`, `#sloth`,
`#wrath`, `#envy`, `#pride`, and `#gooner-trial`, plus private halls and
role-specific channels under the `SINNERS`, `VIRTUES`, `MYTHS`, `DESPAIR`, and
`HOPE` categories.

## 4. Roles created by setup

### Sin trial and final roles

The seven sins are ordered by power from 1 to 7:

| Sin | Trial role | Final role |
|---|---|---|
| Lust | `Trial — Lust` | `Desire Bound Lust` |
| Gluttony | `Trial — Gluttony` | `The Devoured` |
| Greed | `Trial — Greed` | `The False King` |
| Sloth | `Trial — Sloth` | `The vessel of sloth` |
| Wrath | `Trial — Wrath` | `Crimson heir` |
| Envy | `Trial — Envy` | `The Pale Mirror` |
| Pride | `Trial — Pride` | `The bearer of pride` |
| Gooner | `Trial — Gooner` | `The Fox Gooner` |

At 5 or more corruption, the bot uses evolved trial rules where defined.

### Virtue roles

| Opposite sin | Virtue | Role |
|---|---|---|
| Lust | Chastity | `The Chaste` |
| Gluttony | Temperance | `The Fasting King` |
| Greed | Charity | `The Open Hand` |
| Sloth | Diligence | `The Waking` |
| Wrath | Patience | `The Still Flame` |
| Envy | Kindness | `The Mirror's Grace` |
| Pride | Humility | `The Humble Sovereign` |

Standalone virtue roles are `The Scales of Justice`, `The Prudent Eye`,
`The Unbroken`, `The Faithful`, `The Hopeful`, and `The Open Spirit`.

### Myth, despair, hope, and special roles

Setup also creates the La Llorona myth role, `Fallen from Grace`, `Banished`,
`The Ultimate Despair`, `Remnant of Despair`, `Reserve Course Student`,
`Despair Sister`, Izuru's Hope and Despair roles, character Hope/Despair roles,
and the opt-in role:

```text
i SWEAR im not DL bro and i put that on my kids!
```

## 5. Trial summary

| Trial | Requirement |
|---|---|
| Lust | Collect unique heart reactions during the trial window |
| Gluttony | React to every message in `#gluttony-feast` within the allowed window |
| Greed | Use `!kill @user`; expose reactions can open a vote |
| Sloth | Abbreviate words in every message |
| Wrath | Include curse words in every message |
| Envy | Use `!envy_strike @user`; expose reactions can open a vote |
| Pride | Use `!proclaim <words>` and collect bow reactions |
| Gooner | Post image/video attachments in `#gooner-trial`; track with `!gooner_meter` |

Failure adds corruption, triggers a fall, releases the claimed sin, and applies
the configured timeout. Use `!repent` after the timeout to work toward
redemption.

## 6. Command reference

The bot's `!commands` command is the authoritative in-server list. The
commands below are grouped by system and reflect the decorators in the
included Python file.

### Orientation, trials, and progression

```text
!tutorial                 !guide
!sin_tutorial             !sin_guide
!virtue_tutorial          !virtue_guide
!myth_tutorial            !myth_guide
!despair_tutorial         !despair_guide
!hope_tutorial            !hope_guide
!path_tutorial            !path_guide
!role_tutorial            !role_guide / !roles_tutorial
!obtainment              !role_obtainment
!sinslist                !mytrial
!virtueslist             !trial <sin>
!randomtrial             !virtue_trial
!mystats                 !repent [words]
!rankings                !rankings_sins
!history [@user]         !invite
```

### Virtues, bounties, pacts, and administration

```text
!praise @user <message>       !bow_down <message>
!give_role @user <role>       !confirm_give @user
!bounty [@user]               !mybounties
!pact [@user]                 !accept_pact @user
!break_pact                   !pacts
!setup                        !grant @user <sin>
!force_fall @user [reason]    !reset_user @user
!strip_all @user              !release_sin <sin>
!grant_virtue @user <sin>     !grant_special @user <key>
!grant_any_role @user <role>  !fill_meter @user [meter]
!grant_myth @user <myth>      !myths
```

### Sin-specific abilities

```text
!kill @user                   !envy_strike @user
!proclaim <words>             !jealousy_mark @user
!envy_check                   !schizo @user
!weaken <sin>                 !claim @user
!marks [@user]                !gorge
!feast @user                  !devour @user
!purge_bite @user [count]     !expel @user [minutes]
!steal_ability @user          !i_always_get_what_i_want @user <effect>
!frenzy_clash @user           !rage_strike @user
!bloodlust                    !force_lazy @user
!slowdown @user               !force_sleep @user
!deep_sleep                   !sleepwalker
!stop_time [mode]             !recognition
!obsess @user                 !obsession_meter
!obsession_clash              !i_dont_care_if_theyre_watching
!jacobs_ladder @user          !scale_of_wrongdoing @user
!flash @user                  !withered_meat @user
!diane_foxington              !gooner_meter [@user]
!greed_meter                 !sloth_meter
!envy_meter                  !stolen_roles
!wrath_meter                 !gluttony_meter
!pride_meter
```

### Virtue and standalone ability systems

```text
!abstain                      !purify
!fast                         !moderate
!gift_power                   !return_ability
!inspire                      !rouse
!de_escalate                  !absorb_strike
!bless                        !forgive
!submit                       !counter_claim
!discern                      !wise_counsel
!endure                       !fortify
!invoke_faith                 !prayer
!rally                        !beacon
!grant_freedom                !bestow
!verdict                      !divine_retribution
!condemn                      !expose
!anticipate                   !iron_will
!crush                        !smite
!holy_judgment                !inspire_strike
!despair_wave                 !redistribution
!break_chains
```

### Combat, myths, and despair systems

```text
!attack [@user]               !heal
!tragic_event                 !brainwash [@user]
!disaster                     !defend
!summon_sister                !sister_kill [@user]
!sister_say <message>         !sister_seduce [@user]
!sister_anything <action>     !summon_reserve
!student_attack [@user]       !brainwash_remnant [@user]
!drown                        !llorona_wail @user
!llorona_veil                 !llorona_lure @user
```

### Danganronpa characters and talent paths

```text
!characters                   !claim_hope <character>
!claim_despair <character>    !mycharacter
!choose_path <path>           !my_path
!talent_kit [ability]         !talent_ability [@target] [@second]
!path_info                    !coin_power
!support_ability              !attack_ability
!hybrid_ability               !tacht_strike
!tacht_burst                 !reverence_aura
!demand_tribute              !summon_meteor
!izuru_despair                !izuru_hope
!approve_izuru [@user]
```

### Ability menus, economy, appeals, and banishment

```text
!commands                     !target_menu
!ability_category             !ability_categories
!abilities                    !menu
!wallet                       !balance / !coins
!shop                         !buy <item>
!pry                          !release_banished
!call_trial                   !evidence
!disprove                     !juror_support
!appoint_lawyer               !closing_argument
!judge_rule                   !strike_trial
!accept_banishment            !trial_status
```

### Femboy opt-in system

```text
!femboy_opt_in                !femboy_opt_out
!femboy_status                !secretly_watch_femboys / !watch_femboys
!radioactive_substance        !femboy_suggestion / !i_would_like_you_better
!femboy_reject                !pray_testosterone
!it_was_a_bet_bro             !femboy_request_bf
!femboy_accept_bf             !femboy_deny_bf
!femboy_confess               !femboy_accept_confession
```

## Data and deployment notes

- `sins_data.json` is created at runtime and should remain private.
- `.env` files and JSON runtime data are ignored by `.gitignore`.
- Keep `seven_sins_bot.py` as the worker entry point unless you also update
  `Procfile` and the run commands above.