# Zetsubo 2.0 — Seven Deadly Sins Discord Bot

A Railway-ready Python Discord bot. The bot source is `seven_sins_bot.py`.

Use `!myrole` to see your current role, cooldowns, and abilities. The response
includes an interactive button panel: buttons run abilities without arguments,
and abilities that need a target open a small form for a member mention or ID.
Each ability now includes a short explanation in the same response. The
dropdown and button behavior are unchanged. The original `!ability_name` text
commands remain available as a fallback.

## OpenRouter `/ask` command

- Use `/ask question:<text>` for an OpenRouter-powered text reply. Replies are
  posted in the channel; AI-generated mentions are suppressed.
- `/ask` is separate from the existing `!` commands. It cannot inspect server
  state or execute role abilities, moderation, or other server actions.
- Set `OPENROUTER_API_KEY` in Railway Variables, Replit Secrets, or the runtime
  environment. Without it, `/ask` reports that setup is needed; other bot
  commands continue to work.
- `OPENROUTER_MODEL` is optional. The default is
  `google/gemma-4-31b-it:free`; override it with a model ID available on
  OpenRouter. Model availability and pricing can change.
- Invite the bot with the `applications.commands` OAuth2 scope so Discord can
  show `/ask`. The bot syncs its slash command at startup.
- Never commit a real OpenRouter key or put it in this ZIP. OpenRouter usage is
  billed or rate-limited according to the selected model and OpenRouter account.

## Effect cleanup and simplified legacy abilities

- `!remove_effects [@member]` removes only active effects you applied. With no
  member argument, it checks everyone in the current server.
- `!clear_all_effects [@member]` removes all tracked effects from one member or
  everyone in the current server. Only a server administrator or owner can use
  it. The existing `!clear_effects @member` command remains available.
- Effect records now include the server where they were applied, so cleanup
  commands do not clear newly tracked effects from another server.
- Missing abilities from the older sources were added only where they were
  distinct. Their old meters, clash/coin rules, role-removal behavior, and
  unlock gates were replaced with 2.0 status effects. Existing roles and
  abilities, including Kaleb Love's three abilities, remain in place.
- Added simplified role abilities include Greed's `!i_always_get_what_i_want`
  and `!frenzy_clash`, Envy's `!envy_strike`, Prudence's `!expose`,
  Fortitude's `!crush`, Pride's `!demand_tribute`, and Charity's
  source-limited `!return_ability`. Legacy systems such as paths, meters, and
  combat trials were not reintroduced.

The Hope/Despair legacy kit is included in this 2.0 build: Hope's
`!inspire_strike` and `!despair_wave`, the Ultimate Despair event and
brainwashing actions, summoned Sister and Reserve Course actions, and the
interactive text modal for `!sister_say` / `!sister_anything`. Their outcomes
use 2.0 effects such as wards, ability locks, cooldown tax, misfire, and speech
lag rather than the removed HP/damage combat system.

## Deploy through GitHub to Railway

1. Extract this ZIP.
2. Create a new GitHub repository and push the extracted files to it.
3. In Railway, choose **New Project → Deploy from GitHub repo** and select that repository.
4. Add the required Railway variable:
   - `DISCORD_TOKEN` = your bot token from the Discord Developer Portal.
   - `OPENROUTER_API_KEY` = optional OpenRouter key to enable `/ask`.
5. Deploy. Railway will install `requirements.txt` and run `python seven_sins_bot.py`.

Never commit real tokens or API keys or put them in this ZIP. Add them as
Railway variables only.

## Discord setup

In the Discord Developer Portal, enable these privileged intents under **Bot**:

- **Message Content Intent**
- **Server Members Intent**

Include the `applications.commands` OAuth2 scope when inviting the bot to
register `/ask`.

Give the bot the permissions it needs for this bot's features:

- View Channels
- Send Messages
- Read Message History
- Embed Links
- Add Reactions
- Manage Messages
- Manage Roles
- Manage Channels
- Moderate Members

Keep the bot's highest role above the roles it needs to create or manage. Discord will not let a bot assign or edit roles above its own highest role.

## Keeping bot data across Railway restarts

The bot writes state to `sins2_data.json`. Railway's regular filesystem is ephemeral. For persistent state:

1. Add a Railway Volume to the service and mount it at `/data`.
2. Add the Railway variable `RAILWAY_VOLUME_MOUNT_PATH=/data`.
3. Redeploy.

Without a volume, the bot still runs, but role ownership, requests, configuration, and audit data can be lost when Railway recreates the container.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DISCORD_TOKEN="your-token"
python seven_sins_bot.py
```
