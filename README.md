# Seven Deadly Sins Trial Bot

A comprehensive Discord bot based on the **Seven Deadly Sins** with a trial system, role management, sin mechanics, virtue counterparts, bounty system, pact alliances, corruption economy, and a **Danganronpa** character role system.

## Features

### Core Systems
- **Seven Sins & Virtues** — Lust, Gluttony, Greed, Sloth, Wrath, Envy, Pride with evolved trials
- **Corruption Economy** — Permanent failure penalties that compound over time
- **Bounty System** — Place bounties on sin holders
- **Pact Alliances** — Form alliances between sin holders
- **Fall from Grace** — Timeout, redemption, and role stripping on failure

### PVP Combat
- **Universal PVP** — Cross-role combat with `!attack @user`
- **Role-based Stats** — HP, Attack, Defense based on sin/virtue/character roles
- **D20 Combat Rolls** — Critical hits, damage calculations

### Danganronpa Character System
- **14 Characters** — Nagito, Akane, Sonia, Fuyuhiko, Kazuichi, Hiyoko, Mikan, Ibuki, Mahiru, Nekomaru, Gundham, Teruteru, Peko, Chiaki
- **Hope & Despair Versions** — Claim hope first, then convert to despair
- **Chiaki (Ultimate Gamer)** — Pure hope, no despair version

### Izuru Kamakura
- **Izuru Despair** — "How boring..." shock/panic aura, "Who are y-you?" counter
- **Izuru Hope** — Complex obtainment: 32 corruption + 7 hope points + 5 approvals
- **Mastery System** — Locked/unlocked based on Pride trial or Chiaki's fate

## Deployment

### Railway (Recommended)

1. Fork this repo or create a new GitHub repo and push these files
2. In Railway: **New Project** → **Deploy from GitHub repo**
3. Select your repo
4. Add environment variable: `DISCORD_TOKEN` = your bot token
5. Deploy — the bot runs as a background worker automatically

### Local

```bash
pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 seven_sins_bot.py
```

## Required Bot Permissions

Enable these **Privileged Gateway Intents** in the Discord Developer Portal:
- Server Members Intent
- Message Content Intent

Bot permissions needed:
- Manage Roles
- Moderate Members (timeouts)
- Send Messages / Embed Links
- Read Message History
- Add Reactions
- Manage Messages

## Setup

Run `!setup` in your Discord server after adding the bot. This creates all required roles and channels.

## Commands

See [SETUP.md](SETUP.md) for the full command reference.

## Tech Stack

- Python 3.11+
- discord.py 2.3+
- JSON file-based persistence

## License

MIT
