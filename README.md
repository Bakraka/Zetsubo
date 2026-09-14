# Zetsubo — Seven Deadly Sins Trial System

Zetsubo is a Discord roleplay bot built around the Seven Deadly Sins. The
included bot also contains virtue trials, combat, Danganronpa character paths,
myths, despair/hope systems, ability menus, appeals, banishment, and persistent
JSON data.

## Included bot

- Entry point: `seven_sins_bot.py`
- Bot display name: `Zetsubo`
- Prefix: `!`
- Persistent data file: `sins_data.json`
- Required environment variable: `DISCORD_TOKEN`

The Python entry point in this project is the supplied full bot code. The
supporting files in this project are aligned with its actual entry point,
commands, roles, channels, and configuration.

## Features

- Seven sin trials: Lust, Gluttony, Greed, Sloth, Wrath, Envy, and Pride
- Special Gooner trial with attachment tracking
- Evolved trials after corruption and fall-from-grace consequences
- Virtue trials and standalone virtues including Justice, Prudence, Fortitude,
  Faith, Hope, and Liberality
- Sin-specific abilities, meters, clashes, cooldowns, and coin-power mechanics
- Combat, healing, disaster events, and role-based stats
- Danganronpa Hope/Despair characters, talent kits, paths, Izuru, and reserve
  course systems
- Myth system with La Llorona
- Bounties, pacts, rankings, history, appeals, and trial evidence
- Banished role/channel, shop/wallet, tutorials, and interactive ability menus
- Automatic setup of roles, categories, channels, and permissions with `!setup`

## Deploy on Railway

1. Push these files to a repository.
2. Create a Railway project from the repository.
3. Add `DISCORD_TOKEN` as an environment variable.
4. Deploy as a worker. The `Procfile` runs:

```bash
python3 seven_sins_bot.py
```

## Run locally

```bash
python3 -m pip install -r requirements.txt
export DISCORD_TOKEN="your-token-here"
python3 seven_sins_bot.py
```

The bot reads the token from the environment at startup. Do not put the token
in the Python file or commit it to the repository.

## Discord requirements

Enable these privileged gateway intents in the Discord Developer Portal:

- Server Members Intent
- Message Content Intent

The bot needs permission to manage roles and channels, moderate members, read
and send messages, embed links, read message history, add reactions, manage
messages, and attach files. Run `!setup` as a server administrator after the
bot is invited.

## Main commands

Use `!commands` in Discord for the bot's live command list. Useful starting
commands include:

```text
!setup
!tutorial
!sinslist
!virtueslist
!trial <sin>
!randomtrial
!mytrial
!mystats
!rankings
!history [@user]
!characters
!abilities
!wallet
```

The complete command reference is in [SETUP.md](SETUP.md).

## Tech stack

- Python 3.11+
- `discord.py`
- `python-dotenv`
- JSON file-based persistence

## License

MIT