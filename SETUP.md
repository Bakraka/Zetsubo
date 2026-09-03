# Seven Sins Trial System — Setup Guide

## Install

```bash
pip install -r requirements.txt
```

## Configure

Set your bot token as an environment variable or paste it directly in `seven_sins_bot.py`:

```bash
export DISCORD_TOKEN="your-token-here"
```

Or open `seven_sins_bot.py` and set:
```python
TOKEN = "your-token-here"
```

## Required Bot Permissions (Discord Developer Portal)

Enable these **Privileged Gateway Intents**:
- Server Members Intent
- Message Content Intent

Bot permissions needed:
- Manage Roles
- Moderate Members (for timeouts)
- Send Messages / Embed Links
- Read Message History
- Add Reactions
- Manage Messages (to delete command messages for Greed/Envy anonymity)

## Required Roles (create in your Discord server)

Create these roles **exactly** as named (or use `!setup` to auto-create them):

### Trial Placeholder Roles (shown while trial is active)
- `Trial — Lust`
- `Trial — Gluttony`
- `Trial — Greed`
- `Trial — Sloth`
- `Trial — Wrath`
- `Trial — Envy`
- `Trial — Pride`

### Sin Roles (granted after completing trial)
- `Desire Bound Lust` — Lust
- `The Devoured` — Gluttony
- `The False King` — Greed
- `The vessel of sloth` — Sloth
- `Crimson heir` — Wrath
- `The Pale Mirror` — Envy
- `The bearer of pride` — Pride

### Virtue Roles (unlocked after completing a virtue trial)
- `The Chaste` — Chastity (Lust counterpart)
- `The Fasting King` — Temperance (Gluttony counterpart)
- `The Open Hand` — Charity (Greed counterpart)
- `The Waking` — Diligence (Sloth counterpart)
- `The Still Flame` — Patience (Wrath counterpart)
- `The Mirror's Grace` — Kindness (Envy counterpart)
- `The Humble Sovereign` — Humility (Pride counterpart)

### Special Roles
- `Fallen from Grace` — Applied when a member fails their trial

## Required Channels

- `#sins-tribunal` — All announcements, trial starts, falls, completions
- `#gluttony-feast` — Monitored for the Gluttony trial

## Run

```bash
python seven_sins_bot.py
```

---

## Commands

### Player Commands
| Command | Description |
|---|---|
| `!sinslist` | View all sins, their power levels, and claim status |
| `!virtueslist` | View all virtue roles |
| `!trial <sin>` | Begin a trial (e.g. `!trial wrath`) |
| `!randomtrial` | Get a random available trial |
| `!mystats` | View your corruption, trials, cooldowns |
| `!mytrial` | Live status card for your active trial |
| `!repent <words>` | Repent after falling from grace (10 times needed) |
| `!virtue_trial` | Begin your virtue trial after holding a sin role |
| `!rankings` | Full server leaderboard (sin holders, virtues, corruption) |
| `!history [@user]` | View trial history for yourself or another member |
| `!bounty [@user]` | Place or view bounties on sin holders |
| `!pact [@user]` | Propose or view a sin alliance pact |
| `!marks [@user]` | View marks of insecurity for a member |

### Sin-Specific Trial Commands
| Command | Sin | Description |
|---|---|---|
| `!kill @user` | Greed | Anonymously mute someone for 1 hour |
| `!envy_strike @user` | Envy | Anonymously strip a role from someone |
| `!proclaim <words>` | Pride | Post your proclamation to demand bows |

### Unlocked Role Abilities (after fully completing a sin trial)
| Command | Sin | Description |
|---|---|---|
| `!jealousy_mark @user` | Envy | Mark someone with jealousy — if they don't `!envy_check` in 30 min, you steal their role temporarily. Cannot target Pride. 1-day per-role cooldown. |
| `!envy_check` | Any | Check if you are the jealousy mark target. If you are, Envy gains +1 corruption and loses their ability for 1 hour. |
| `!schizo @user` | Envy | Flood the channel with phantom messages targeting someone. Uses **coin flip** (2 coins). If they resist, it backfires. |
| `!weaken <sin>` | Pride | Reduce a sin's effective power by 1 for 30 minutes. Uses **coin flip** (3 coins). |
| `!claim @user` | Pride | Claim a target — all sin holders with equal or lower power must bow (🙇) within their time window or gain a Mark of Insecurity. 2-hour cooldown. No coin flip. |

### Virtue Commands
| Command | Sin | Description |
|---|---|---|
| `!praise @user <msg>` | Envy virtue | Publicly compliment someone (10+ words) |
| `!bow_down <msg>` | Pride virtue | Post a public act of humility |
| `!give_role @user <role>` | Greed virtue | Give away one of your roles |

### Admin Commands
| Command | Description |
|---|---|
| `!setup` | Auto-create all required roles and channels |
| `!grant @user <sin>` | Force-complete a trial for a member |
| `!force_fall @user [reason]` | Manually trigger a fall from grace |
| `!reset_user @user` | Wipe all sin data for a user |
| `!release_sin <sin>` | Unclaim a sin (remove from current holder) |
| `!grant_virtue @user <sin>` | Force-grant a virtue role |
| `!confirm_give @user` | Confirm a Greed virtue role transfer |

---

## Trial Mechanics Summary

| Sin | Power | Trial | Duration | Expose Risk |
|---|---|---|---|---|
| Lust | 1 | Collect 5 unique ❤️ reactions on your messages | 24h | No |
| Gluttony | 2 | React to every message in #gluttony-feast within 5min | 24h | No |
| Greed | 3 | Secretly mute someone with `!kill` | 8h | Yes |
| Sloth | 4 | Abbreviate every word (max 4 chars) in all messages | 48h | No |
| Wrath | 5 | Every message must contain a curse word | 24h | No |
| Envy | 6 | Secretly strip a role with `!envy_strike` | 12h | Yes |
| Pride | 7 | Get 60% of online members to react 🙇 to your proclamation | 48h | No |

## Virtue Summary

| Virtue | Role Nickname | Opposite Sin | Trial |
|---|---|---|---|
| Chastity | The Chaste | Lust | No ❤️ reactions for 48h |
| Temperance | The Fasting King | Gluttony | No reactions at all for 24h |
| Charity | The Open Hand | Greed | Give away one of your roles |
| Diligence | The Waking | Sloth | 20+ word message every hour for 24h |
| Patience | The Still Flame | Wrath | No curse words for 48h |
| Kindness | The Mirror's Grace | Envy | Praise 5 different members (10+ words each) |
| Humility | The Humble Sovereign | Pride | Get 10 members to react 🙏 to your bow |

---

## Coin Flip System

When an ability uses the coin flip, both sides roll **d20** dice. Whoever rolls higher gets to apply their effect on the other. Ties are **rerolled**.

Some abilities have **more coins** = they roll more dice and take the highest number, giving better odds.

| Ability | Coins |
|---|---|
| `!schizo` (Envy) | 2 coins |
| `!weaken` (Pride) | 3 coins |
| Target (defender) | 1–2 coins (scales with power) |

**Coin flip does NOT apply to:**
- `!jealousy_mark` / `!claim` (Pride 2nd ability)

---

## Marks of Insecurity & Krodingers Effect

The Pride `!claim` ability forces lower-power sin holders to bow. Failure earns a **Mark of Insecurity**.

When marks reach the threshold (depends on the marked player's power), **Krodingers Effect** triggers:
- Their ability is **locked for 30 minutes**
- Marks reset to 0

| Power Level | Marks to Trigger |
|---|---|
| 7 | 3 marks |
| 5–6 | 4 marks |
| 3–4 | 5 marks |
| 1–2 | 6 marks |

View marks with `!marks` or `!marks @user`.

---

## Corruption System

- Every failure adds **permanent corruption points**
- At **5+ corruption**, trials switch to **Evolved** (harder) mode
- Cooldowns multiply with corruption: `base_hours × (1 + corruption ÷ 5)`
- Corruption never resets — it compounds forever

## Fall from Grace

When you fail a trial:
1. All sin/virtue roles are **stripped**
2. You receive the `Fallen from Grace` role
3. You are **timed out for 3 days**
4. You must use `!repent` **10 times** (after the timeout expires) to return to @everyone
5. The sin you held is **released** for others to claim
