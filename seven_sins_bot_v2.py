"""Sins Bot 2.0

A deliberately smaller role-and-ability bot.

Removed from 2.0:
- trial timers and trial channels
- combat and HP
- evolved/second modes and power-up gates
- global mute state

Kept in 2.0:
- one holder per managed role
- role requests, approvals, owner grants, and Discord-role reconciliation
- role abilities with cooldown, slow, proc-penalty, mute, timeout, and
  channel-removal effects
- a private /myabilities menu with clickable command hints
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

BOT_NAME = "Zetsubo — Sins Bot 2.0"
DATA_FILE = "sins_v2_data.json"
PREFIX = "!"
HIGH_POWER_THRESHOLD = 5


ROLE_DEFINITIONS: dict[str, dict] = {
    "lust": {
        "name": "Desire Bound Lust",
        "power": 1,
        "abilities": ["obsess", "obsession_clash"],
    },
    "gluttony": {
        "name": "The Devoured",
        "power": 2,
        "abilities": ["gorge", "feast", "devour", "purge_bite", "expel"],
    },
    "greed": {
        "name": "The False King",
        "power": 3,
        "abilities": [
            "steal_ability",
            "i_always_get_what_i_want",
            "frenzy_clash",
        ],
    },
    "sloth": {
        "name": "The vessel of sloth",
        "power": 4,
        "abilities": [
            "force_lazy",
            "slowdown",
            "force_sleep",
            "deep_sleep",
            "sleepwalker",
        ],
    },
    "wrath": {
        "name": "Crimson heir",
        "power": 5,
        "abilities": ["rage_strike", "bloodlust", "summon_meteor"],
    },
    "envy": {
        "name": "The Pale Mirror",
        "power": 6,
        "abilities": ["jealousy_mark", "envy_check", "schizo"],
    },
    "pride": {
        "name": "The bearer of pride",
        "power": 7,
        "abilities": ["weaken", "claim", "stop_time"],
    },
    "chastity": {
        "name": "The Chaste",
        "power": 8,
        "abilities": ["abstain", "purify"],
    },
    "temperance": {
        "name": "The Fasting King",
        "power": 9,
        "abilities": ["fast", "moderate"],
    },
    "charity": {
        "name": "The Open Hand",
        "power": 10,
        "abilities": ["gift_power", "return_ability"],
    },
    "diligence": {
        "name": "The Waking",
        "power": 11,
        "abilities": ["inspire", "rouse"],
    },
    "patience": {
        "name": "The Still Flame",
        "power": 12,
        "abilities": ["de_escalate", "absorb_strike"],
    },
    "kindness": {
        "name": "The Mirror's Grace",
        "power": 13,
        "abilities": ["bless", "forgive"],
    },
    "humility": {
        "name": "The Humble Sovereign",
        "power": 14,
        "abilities": ["submit", "counter_claim"],
    },
    "justice": {
        "name": "The Scales of Justice",
        "power": 15,
        "abilities": ["discern", "wise_counsel", "verdict"],
    },
    "prudence": {
        "name": "The Prudent Eye",
        "power": 16,
        "abilities": ["anticipate", "return_ability"],
    },
    "fortitude": {
        "name": "The Unbroken",
        "power": 17,
        "abilities": ["endure", "fortify"],
    },
    "faith": {
        "name": "The Faithful",
        "power": 18,
        "abilities": ["invoke_faith", "prayer", "rally"],
    },
    "hope": {
        "name": "The Hopeful",
        "power": 19,
        "abilities": ["beacon", "grant_freedom"],
    },
    "liberality": {
        "name": "The Open Spirit",
        "power": 20,
        "abilities": ["bestow", "redistribution", "break_chains"],
    },
    "despair": {
        "name": "The Ultimate Despair",
        "power": 21,
        "abilities": ["condemn", "despair_wave"],
    },
    "izuru_despair": {
        "name": "Izuru Kamakura: Remnant of Despair",
        "power": 22,
        "abilities": ["holy_judgment", "divine_retribution"],
    },
    "izuru_hope": {
        "name": "Izuru Kamakura: Ultimate Hope",
        "power": 23,
        "abilities": ["inspire_strike", "break_chains"],
    },
}

ROLE_ALIASES = {
    "the_chaste": "chastity",
    "the_fasting_king": "temperance",
    "the_open_hand": "charity",
    "the_waking": "diligence",
    "the_still_flame": "patience",
    "the_mirrors_grace": "kindness",
    "the_humble_sovereign": "humility",
    "scales": "justice",
    "prudent_eye": "prudence",
    "unbroken": "fortitude",
    "faithful": "faith",
    "hopeful": "hope",
    "open_spirit": "liberality",
    "ultimate_despair": "despair",
    "remnant_of_despair": "izuru_despair",
    "ultimate_hope": "izuru_hope",
}


# These are the former path moves. They are now base moves for every managed
# role; there is no path selection or second-mode unlock requirement.
BASE_PATH_ABILITIES = {
    "support_ability": "Apply a short cooldown increase to an ally's abilities.",
    "attack_ability": "Apply a short proc penalty to a target's next abilities.",
    "hybrid_ability": "Apply both a cooldown increase and proc penalty.",
    "tacht_strike": "Briefly lock a target's abilities.",
    "tacht_burst": "Mute a target for a short duration.",
    "reverence_aura": "Reduce nearby pressure by clearing your own effects.",
    "demand_tribute": "Make a target's abilities slower to activate.",
    "summon_meteor": "Temporarily remove a target from the current channel.",
}


def _ability(
    roles: tuple[str, ...] | None,
    description: str,
    effect: str,
    *,
    cooldown: int = 60,
    duration: int = 120,
    needs_target: bool = True,
    aliases: tuple[str, ...] = (),
) -> dict:
    return {
        "roles": roles,
        "description": description,
        "effect": effect,
        "cooldown": cooldown,
        "duration": duration,
        "needs_target": needs_target,
        "aliases": aliases,
    }


ABILITY_DEFINITIONS: dict[str, dict] = {
    # Sin abilities. None of these deal HP damage in 2.0.
    "obsess": _ability(("lust",), "Slow a target's ability recovery.", "cooldown_slow", duration=300),
    "obsession_clash": _ability(
        ("lust",), "Make a target's next abilities less likely to proc.", "proc_penalty", duration=300
    ),
    "gorge": _ability(("gluttony",), "Clear your own temporary effects.", "clear_self", needs_target=False),
    "feast": _ability(("gluttony",), "Make a target's moves slower.", "cooldown_slow", duration=360),
    "devour": _ability(("gluttony",), "Mute a target briefly.", "mute", duration=300),
    "purge_bite": _ability(("gluttony",), "Remove a target from this channel briefly.", "channel_kick", duration=120),
    "expel": _ability(("gluttony",), "Timeout a target briefly.", "timeout", duration=300),
    "steal_ability": _ability(("greed",), "Lock a target's current abilities.", "ability_lock", duration=300),
    "i_always_get_what_i_want": _ability(
        ("greed",), "Make a target's moves slower and less reliable.", "heavy_slow", duration=420
    ),
    "frenzy_clash": _ability(("greed",), "Apply a heavy cooldown to a target.", "heavy_cooldown", duration=300),
    "force_lazy": _ability(("sloth",), "Add a delay after a target types.", "typing_delay", duration=300),
    "slowdown": _ability(("sloth",), "Make a target's ability cooldowns longer.", "cooldown_slow", duration=420),
    "force_sleep": _ability(("sloth",), "Lock a target's abilities.", "ability_lock", duration=900),
    "deep_sleep": _ability(("sloth",), "Apply a longer ability lock.", "ability_lock", duration=1800),
    "sleepwalker": _ability(("sloth",), "Clear your own typing delay and slow effects.", "clear_self", needs_target=False),
    "rage_strike": _ability(("wrath",), "Force a target into a short timeout.", "timeout", duration=120),
    "bloodlust": _ability(("wrath",), "Make your next ability recover faster.", "self_haste", needs_target=False),
    "summon_meteor": _ability(
        ("wrath",), "Temporarily remove a target from this channel.", "channel_kick", duration=180
    ),
    "jealousy_mark": _ability(("envy",), "Make a target less likely to proc abilities.", "proc_penalty", duration=600),
    "envy_check": _ability(("envy",), "Clear your own negative effects.", "clear_self", needs_target=False),
    "schizo": _ability(("envy",), "Lock a target's abilities briefly.", "ability_lock", duration=300),
    "weaken": _ability(("pride",), "Make a target's abilities slower.", "cooldown_slow", duration=1800),
    "claim": _ability(("pride",), "Apply a heavy cooldown and proc penalty.", "heavy_slow", duration=600),
    "stop_time": _ability(("pride",), "Temporarily stop a target's abilities.", "ability_lock", duration=120),

    # Virtue and standalone-role moves.
    "abstain": _ability(("chastity",), "Clear your own temporary effects.", "clear_self", needs_target=False),
    "purify": _ability(("chastity",), "Clear a target's slow or proc penalty.", "clear_target"),
    "fast": _ability(("temperance",), "Reduce a target's ability recovery.", "cooldown_slow", duration=120),
    "moderate": _ability(("temperance",), "Clear a target's ability lock.", "clear_target"),
    "gift_power": _ability(("charity",), "Give an ally a temporary cooldown reduction.", "ally_haste"),
    "return_ability": _ability(("charity", "prudence"), "Clear a target's ability lock.", "clear_target"),
    "inspire": _ability(("diligence",), "Give an ally faster ability recovery.", "ally_haste"),
    "rouse": _ability(("diligence",), "Clear an ally's typing delay.", "clear_target"),
    "de_escalate": _ability(("patience",), "Remove a target's proc penalty.", "clear_target"),
    "absorb_strike": _ability(("patience",), "Reduce a target's cooldown slow.", "clear_target"),
    "bless": _ability(("kindness",), "Give an ally faster recovery.", "ally_haste"),
    "forgive": _ability(("kindness",), "Clear a target's negative effects.", "clear_target"),
    "submit": _ability(("humility",), "Clear your own effects.", "clear_self", needs_target=False),
    "counter_claim": _ability(("humility",), "Lock the claimant's abilities.", "ability_lock"),
    "discern": _ability(("justice",), "Reveal a target's active effect summary.", "inspect"),
    "wise_counsel": _ability(("justice",), "Clear a target's cooldown slow.", "clear_target"),
    "verdict": _ability(("justice",), "Apply a short ability lock.", "ability_lock"),
    "anticipate": _ability(("prudence",), "Give yourself a short proc shield.", "self_haste", needs_target=False),
    "endure": _ability(("fortitude",), "Clear your own locks and slow effects.", "clear_self", needs_target=False),
    "fortify": _ability(("fortitude",), "Give an ally a proc shield.", "ally_haste"),
    "invoke_faith": _ability(("faith",), "Clear an ally's negative effects.", "clear_target"),
    "prayer": _ability(("faith",), "Give an ally faster recovery.", "ally_haste"),
    "rally": _ability(("faith",), "Clear a target's ability lock.", "clear_target"),
    "beacon": _ability(("hope",), "Clear your own negative effects.", "clear_self", needs_target=False),
    "grant_freedom": _ability(("hope",), "Clear a target's lock and timeout.", "freedom"),
    "bestow": _ability(("liberality",), "Give an ally a short proc shield.", "ally_haste"),
    "redistribution": _ability(("liberality",), "Move your own cooldown pressure to a target.", "heavy_cooldown"),
    "break_chains": _ability(("liberality", "izuru_hope"), "Clear a target's locks.", "freedom"),
    "condemn": _ability(("despair",), "Apply a heavy cooldown.", "heavy_cooldown"),
    "despair_wave": _ability(("despair",), "Apply a proc penalty to a target.", "proc_penalty", duration=600),
    "divine_retribution": _ability(("izuru_despair",), "Temporarily lock a target.", "ability_lock", duration=600),
    "holy_judgment": _ability(("izuru_despair",), "Temporarily remove a target from this channel.", "channel_kick", duration=180),
    "inspire_strike": _ability(("izuru_hope",), "Clear a target's negative effects.", "freedom"),

    # Former path moves are available to every managed-role holder.
    "support_ability": _ability((), BASE_PATH_ABILITIES["support_ability"], "ally_haste"),
    "attack_ability": _ability((), BASE_PATH_ABILITIES["attack_ability"], "proc_penalty"),
    "hybrid_ability": _ability((), BASE_PATH_ABILITIES["hybrid_ability"], "heavy_slow"),
    "tacht_strike": _ability((), BASE_PATH_ABILITIES["tacht_strike"], "ability_lock"),
    "tacht_burst": _ability((), BASE_PATH_ABILITIES["tacht_burst"], "mute", duration=90),
    "reverence_aura": _ability((), BASE_PATH_ABILITIES["reverence_aura"], "clear_self", needs_target=False),
    "demand_tribute": _ability((), BASE_PATH_ABILITIES["demand_tribute"], "cooldown_slow"),
}


def now_ts() -> float:
    return time.time()


def empty_state() -> dict:
    return {"role_holders": {}, "requests": {}, "users": {}}


def load_state() -> dict:
    if not os.path.exists(DATA_FILE):
        return empty_state()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        state.setdefault("role_holders", {})
        state.setdefault("requests", {})
        state.setdefault("users", {})
        return state
    except (OSError, json.JSONDecodeError):
        return empty_state()


def save_state(state: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def get_user(state: dict, member_id: int) -> dict:
    user = state["users"].setdefault(
        str(member_id),
        {
            "cooldowns": {},
            "effects": {},
            "last_message_at": 0,
        },
    )
    user.setdefault("cooldowns", {})
    user.setdefault("effects", {})
    user.setdefault("last_message_at", 0)
    return user


def normalize(value: str) -> str:
    return "_".join(value.strip().lower().replace("—", " ").split())


def resolve_role_key(value: str) -> Optional[str]:
    key = normalize(value)
    if key in ROLE_DEFINITIONS:
        return key
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    for role_key, definition in ROLE_DEFINITIONS.items():
        if normalize(definition["name"]) == key:
            return role_key
    return None


def role_label(role_key: str) -> str:
    return ROLE_DEFINITIONS[role_key]["name"]


def get_role(guild: discord.Guild, role_key: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=role_label(role_key))


def effect_active(user: dict, effect_name: str) -> bool:
    return float(user["effects"].get(effect_name, 0) or 0) > now_ts()


def effect_until(user: dict, effect_name: str, duration: int) -> None:
    user["effects"][effect_name] = max(
        float(user["effects"].get(effect_name, 0) or 0),
        now_ts() + duration,
    )


def has_managed_role(member: discord.Member, role_key: str) -> bool:
    role = get_role(member.guild, role_key)
    return role is not None and role in member.roles


def held_role_keys(member: discord.Member) -> list[str]:
    return [key for key in ROLE_DEFINITIONS if has_managed_role(member, key)]


def display_remaining(until: float) -> str:
    seconds = max(0, int(until - now_ts()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
slash_commands_synced = False


async def ensure_role(guild: discord.Guild, role_key: str) -> Optional[discord.Role]:
    role = get_role(guild, role_key)
    if role:
        return role
    try:
        return await guild.create_role(
            name=role_label(role_key),
            reason=f"{BOT_NAME} role setup",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None


async def assign_managed_role(
    member: discord.Member,
    role_key: str,
    state: dict,
    reason: str,
) -> tuple[bool, str]:
    guild = member.guild
    role = await ensure_role(guild, role_key)
    if role is None:
        return False, f"I could not find or create **{role_label(role_key)}**."
    if guild.me is None or role >= guild.me.top_role:
        return False, f"My highest role must be above **{role_label(role_key)}**."

    holder_id = state["role_holders"].get(role_key)
    if holder_id and int(holder_id) != member.id:
        current = guild.get_member(int(holder_id))
        if current and role in current.roles:
            return False, f"**{role_label(role_key)}** is already held by {current.mention}."
        state["role_holders"].pop(role_key, None)

    if role not in member.roles:
        try:
            await member.add_roles(role, reason=reason)
        except (discord.Forbidden, discord.HTTPException) as exc:
            return False, f"Discord could not assign the role: `{type(exc).__name__}`."

    # Verify against Discord after assignment. This prevents the old silent
    # success bug where local state changed but the role was not actually added.
    try:
        verified = await guild.fetch_member(member.id)
    except (discord.Forbidden, discord.HTTPException):
        verified = member
    if role.id not in {item.id for item in verified.roles}:
        return False, f"Discord did not confirm **{role_label(role_key)}** on {member.mention}."

    state["role_holders"][role_key] = str(member.id)
    return True, f"✅ **{role_label(role_key)}** is now held by {member.mention}."


async def remove_managed_role(member: discord.Member, role_key: str, state: dict, reason: str) -> tuple[bool, str]:
    role = get_role(member.guild, role_key)
    if role is None or role not in member.roles:
        state["role_holders"].pop(role_key, None)
        return False, f"{member.mention} does not hold **{role_label(role_key)}**."
    try:
        await member.remove_roles(role, reason=reason)
    except (discord.Forbidden, discord.HTTPException) as exc:
        return False, f"Discord could not remove the role: `{type(exc).__name__}`."
    state["role_holders"].pop(role_key, None)
    return True, f"✅ Removed **{role_label(role_key)}** from {member.mention}."


def approvals_required(role_key: str) -> int:
    power = ROLE_DEFINITIONS[role_key]["power"]
    return max(2, min(6, power - HIGH_POWER_THRESHOLD + 1))


def request_id(guild_id: int, role_key: str) -> str:
    return f"{guild_id}:{role_key}"


def active_role_holder(guild: discord.Guild, state: dict, role_key: str) -> Optional[discord.Member]:
    holder_id = state["role_holders"].get(role_key)
    if not holder_id:
        return None
    return guild.get_member(int(holder_id))


@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup(ctx: commands.Context) -> None:
    """Create the managed roles only; 2.0 has no trial-channel setup."""
    created: list[str] = []
    existing: list[str] = []
    for role_key in ROLE_DEFINITIONS:
        role = get_role(ctx.guild, role_key)
        if role:
            existing.append(role_label(role_key))
        elif await ensure_role(ctx.guild, role_key):
            created.append(role_label(role_key))
    await reconcile_guild_roles(ctx.guild)
    await ctx.send(
        f"✅ Sins Bot 2.0 setup complete.\n"
        f"Created: **{len(created)}** roles | Already present: **{len(existing)}** roles.\n"
        "No trial channels or combat systems were created."
    )


@bot.command(name="roles")
async def roles(ctx: commands.Context) -> None:
    """List managed roles and current holders."""
    state = load_state()
    lines = []
    for key, definition in sorted(ROLE_DEFINITIONS.items(), key=lambda item: item[1]["power"]):
        holder = active_role_holder(ctx.guild, state, key)
        holder_text = holder.mention if holder else "available"
        lines.append(f"`{key}` — **{definition['name']}** (P{definition['power']}) — {holder_text}")
    await ctx.send(embed=discord.Embed(
        title="🎭 Managed Roles",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    ))


@bot.command(name="myroles")
async def myroles(ctx: commands.Context) -> None:
    keys = held_role_keys(ctx.author)
    text = "\n".join(f"• **{role_label(key)}** (`{key}`)" for key in keys)
    await ctx.send(
        f"Your managed roles:\n{text or '*(none)*'}\n\n"
        "Use `!request_role <role>` to ask for an available role."
    )


@bot.command(name="request_role")
async def request_role(ctx: commands.Context, role: str) -> None:
    """Auto-grant low-power roles or open an approval request for high-power roles."""
    role_key = resolve_role_key(role)
    if not role_key:
        await ctx.send("❌ Unknown role key. Use `!roles` to see valid keys.", delete_after=10)
        return
    state = load_state()
    existing_holder = active_role_holder(ctx.guild, state, role_key)
    if existing_holder and existing_holder.id != ctx.author.id:
        await ctx.send(f"❌ **{role_label(role_key)}** is already held by {existing_holder.mention}.")
        return

    power = ROLE_DEFINITIONS[role_key]["power"]
    if power < HIGH_POWER_THRESHOLD:
        ok, message = await assign_managed_role(
            ctx.author,
            role_key,
            state,
            reason=f"!request_role by {ctx.author}",
        )
        save_state(state)
        await ctx.send(message)
        return

    key = request_id(ctx.guild.id, role_key)
    pending = state["requests"].get(key)
    if pending and pending["requester_id"] != str(ctx.author.id):
        await ctx.send("❌ That role already has a pending request.")
        return
    if not pending:
        pending = {
            "guild_id": str(ctx.guild.id),
            "role_key": role_key,
            "requester_id": str(ctx.author.id),
            "approvals": [],
            "created_at": now_ts(),
        }
        state["requests"][key] = pending
    save_state(state)
    required = approvals_required(role_key)
    await ctx.send(
        f"📨 {ctx.author.mention} requested **{role_label(role_key)}** (power {power}). "
        f"It needs **{required}** unique approvals.\n"
        f"Approvers can use `!approve_role {ctx.author.mention} {role_key}`. "
        "The server owner can use `!owner_grant_role` instead."
    )


@bot.command(name="approve_role")
async def approve_role(ctx: commands.Context, member: discord.Member, role: str) -> None:
    role_key = resolve_role_key(role)
    if not role_key:
        await ctx.send("❌ Unknown role key.", delete_after=8)
        return
    state = load_state()
    pending = state["requests"].get(request_id(ctx.guild.id, role_key))
    if not pending or pending["requester_id"] != str(member.id):
        await ctx.send("❌ There is no matching pending request.", delete_after=8)
        return
    if ctx.author.id == member.id:
        await ctx.send("❌ You cannot approve your own role request.", delete_after=8)
        return
    if str(ctx.author.id) not in pending["approvals"]:
        pending["approvals"].append(str(ctx.author.id))
    required = approvals_required(role_key)
    if len(pending["approvals"]) >= required:
        ok, message = await assign_managed_role(
            member,
            role_key,
            state,
            reason=f"Approved role request by {ctx.author}",
        )
        state["requests"].pop(request_id(ctx.guild.id, role_key), None)
        save_state(state)
        await ctx.send(message)
        return
    save_state(state)
    await ctx.send(
        f"✅ Approval recorded: **{len(pending['approvals'])}/{required}** "
        f"for {member.mention}'s **{role_label(role_key)}** request."
    )


@bot.command(name="owner_grant_role")
async def owner_grant_role(ctx: commands.Context, member: discord.Member, role: str) -> None:
    """Server-owner override for any managed role request."""
    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send("❌ Only the server owner can use this command.", delete_after=8)
        return
    role_key = resolve_role_key(role)
    if not role_key:
        await ctx.send("❌ Unknown role key.", delete_after=8)
        return
    state = load_state()
    ok, message = await assign_managed_role(
        member,
        role_key,
        state,
        reason=f"Owner grant by {ctx.author}",
    )
    state["requests"].pop(request_id(ctx.guild.id, role_key), None)
    save_state(state)
    await ctx.send(message)


@bot.command(name="release_role")
async def release_role(ctx: commands.Context, role: str) -> None:
    role_key = resolve_role_key(role)
    if not role_key:
        await ctx.send("❌ Unknown role key.", delete_after=8)
        return
    state = load_state()
    holder = active_role_holder(ctx.guild, state, role_key)
    if holder is None:
        await ctx.send("That role is already available.", delete_after=8)
        return
    if holder.id != ctx.author.id and ctx.guild.owner_id != ctx.author.id:
        await ctx.send("❌ Only the role holder or server owner can release it.", delete_after=8)
        return
    ok, message = await remove_managed_role(
        holder,
        role_key,
        state,
        reason=f"!release_role by {ctx.author}",
    )
    save_state(state)
    await ctx.send(message)


@bot.command(name="owner_strip", aliases=["owner_strip_all"])
async def owner_strip(ctx: commands.Context) -> None:
    """Server-owner-only personal strip for every manageable Discord role."""
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used inside a server.", delete_after=8)
        return
    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send("❌ Only the server owner can use this command.", delete_after=8)
        return
    if ctx.guild.me is None:
        await ctx.send("❌ I cannot verify my role position right now.", delete_after=8)
        return

    state = load_state()
    removable = [
        role for role in ctx.author.roles
        if role != ctx.guild.default_role
        and not role.managed
        and role < ctx.guild.me.top_role
    ]
    skipped = [
        role for role in ctx.author.roles
        if role != ctx.guild.default_role
        and (role.managed or role >= ctx.guild.me.top_role)
    ]
    removed: list[str] = []
    errors: list[str] = []
    for role in removable:
        try:
            await ctx.author.remove_roles(role, reason=f"Sins 2.0 owner strip by {ctx.author}")
            removed.append(role.name)
            for role_key, definition in ROLE_DEFINITIONS.items():
                if definition["name"] == role.name and state["role_holders"].get(role_key) == str(ctx.author.id):
                    state["role_holders"].pop(role_key, None)
        except (discord.Forbidden, discord.HTTPException) as exc:
            errors.append(f"{role.name} ({type(exc).__name__})")
    save_state(state)

    embed = discord.Embed(
        title="🧹 Owner Roles Stripped",
        description="Only the invoking server owner was targeted. `@everyone` is retained.",
        color=discord.Color.dark_red(),
    )
    embed.add_field(
        name=f"Removed ({len(removed)})",
        value=", ".join(removed) if removed else "No manageable roles found.",
        inline=False,
    )
    if skipped:
        embed.add_field(
            name=f"Skipped ({len(skipped)})",
            value=", ".join(role.name for role in skipped),
            inline=False,
        )
    if errors:
        embed.add_field(name=f"Errors ({len(errors)})", value=", ".join(errors), inline=False)
    await ctx.send(embed=embed)


async def reconcile_member_roles(member: discord.Member, state: Optional[dict] = None) -> None:
    """Keep persistent ownership aligned with actual Discord role membership."""
    state = state or load_state()
    changed = False
    for role_key in ROLE_DEFINITIONS:
        role = get_role(member.guild, role_key)
        if role is None:
            continue
        holder_id = state["role_holders"].get(role_key)
        has_role = role in member.roles
        if has_role and not holder_id:
            state["role_holders"][role_key] = str(member.id)
            changed = True
        elif has_role and holder_id and int(holder_id) != member.id:
            current = member.guild.get_member(int(holder_id))
            if current and role in current.roles:
                try:
                    await member.remove_roles(role, reason="Sins 2.0 single-holder protection")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            else:
                state["role_holders"][role_key] = str(member.id)
                changed = True
        elif not has_role and holder_id and int(holder_id) == member.id:
            state["role_holders"].pop(role_key, None)
            changed = True
    if changed:
        save_state(state)


async def reconcile_guild_roles(guild: discord.Guild) -> None:
    """Repair stale ownership and remove duplicate managed roles."""
    state = load_state()
    for role_key in ROLE_DEFINITIONS:
        role = get_role(guild, role_key)
        if role is None:
            state["role_holders"].pop(role_key, None)
            continue
        holders = [member for member in guild.members if role in member.roles]
        preferred_id = state["role_holders"].get(role_key)
        keeper = next((m for m in holders if str(m.id) == str(preferred_id)), None)
        keeper = keeper or (holders[0] if holders else None)
        if keeper:
            state["role_holders"][role_key] = str(keeper.id)
            for duplicate in holders:
                if duplicate.id == keeper.id or role.managed or role >= guild.me.top_role:
                    continue
                try:
                    await duplicate.remove_roles(role, reason="Sins 2.0 duplicate-role repair")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        else:
            state["role_holders"].pop(role_key, None)
    save_state(state)


@bot.command(name="sync_roles", aliases=["repair_roles"])
@commands.has_permissions(administrator=True)
async def sync_roles(ctx: commands.Context) -> None:
    await reconcile_guild_roles(ctx.guild)
    await ctx.send("✅ Managed-role ownership was reconciled with Discord.")


@bot.command(name="role_requests")
async def role_requests(ctx: commands.Context) -> None:
    state = load_state()
    pending = [
        item for item in state["requests"].values()
        if item.get("guild_id") == str(ctx.guild.id)
    ]
    if not pending:
        await ctx.send("There are no pending high-power role requests.")
        return
    lines = []
    for item in pending:
        role_key = item["role_key"]
        requester = ctx.guild.get_member(int(item["requester_id"]))
        lines.append(
            f"**{role_label(role_key)}** — {requester.mention if requester else 'unknown'} — "
            f"{len(item['approvals'])}/{approvals_required(role_key)} approvals"
        )
    await ctx.send("\n".join(lines))


def _target_from_context(ctx: commands.Context) -> Optional[discord.Member]:
    return ctx.message.mentions[0] if ctx.message.mentions else None


def _ability_keys_for_member(member: discord.Member) -> list[str]:
    keys: list[str] = []
    held = set(held_role_keys(member))
    for name, definition in ABILITY_DEFINITIONS.items():
        roles = definition["roles"]
        if roles is None or not roles or held.intersection(roles):
            keys.append(name)
    return sorted(set(keys))


class AbilityHintButton(discord.ui.Button):
    def __init__(self, owner_id: int, command_name: str, description: str):
        super().__init__(
            label=command_name[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"sins2:ability:{owner_id}:{command_name}",
        )
        self.owner_id = owner_id
        self.command_name = command_name
        self.description = description

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This ability menu belongs to someone else. Run `/myabilities` for your own.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Type `{PREFIX}{self.command_name}` yourself.\n{self.description}",
            ephemeral=True,
        )


class AbilityMenu(discord.ui.View):
    def __init__(self, owner_id: int, ability_keys: list[str]):
        super().__init__(timeout=300)
        for key in ability_keys[:25]:
            self.add_item(
                AbilityHintButton(
                    owner_id,
                    key,
                    ABILITY_DEFINITIONS[key]["description"],
                )
            )


def build_ability_embed(member: discord.Member) -> tuple[discord.Embed, Optional[AbilityMenu]]:
    keys = _ability_keys_for_member(member)
    role_lines = [
        f"• **{role_label(key)}** (`{key}`)"
        for key in held_role_keys(member)
    ]
    embed = discord.Embed(
        title=f"📖 {member.display_name}'s Abilities",
        description="\n".join(role_lines) or "No managed role is currently assigned.",
        color=discord.Color.blurple(),
    )
    if keys:
        embed.add_field(
            name=f"Available abilities ({len(keys)})",
            value="\n".join(f"`{PREFIX}{key}` — {ABILITY_DEFINITIONS[key]['description']}" for key in keys[:25]),
            inline=False,
        )
        if len(keys) > 25:
            embed.add_field(
                name="More abilities",
                value=f"Use `{PREFIX}abilities` or `/myabilities` again; the full list is also available with `{PREFIX}ability_list`.",
                inline=False,
            )
    else:
        embed.add_field(
            name="No abilities yet",
            value=f"Use `{PREFIX}roles` and `{PREFIX}request_role <role>` to obtain a managed role.",
            inline=False,
        )
    embed.set_footer(text="Buttons only show command syntax. They never execute an ability.")
    return embed, AbilityMenu(member.id, keys) if keys else None


@bot.command(name="abilities", aliases=["myabilities"])
async def abilities(ctx: commands.Context) -> None:
    embed, view = build_ability_embed(ctx.author)
    await ctx.send(embed=embed, view=view)


@bot.tree.command(
    name="myabilities",
    description="Privately list your current Sins Bot 2.0 abilities.",
)
async def myabilities(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
        return
    embed, view = build_ability_embed(member)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.command(name="ability_list")
async def ability_list(ctx: commands.Context) -> None:
    keys = _ability_keys_for_member(ctx.author)
    await ctx.send(
        "\n".join(f"`{PREFIX}{key}` — {ABILITY_DEFINITIONS[key]['description']}" for key in keys)
        or "You have no managed-role abilities."
    )


def effect_text(effect: str, duration: int) -> str:
    if effect == "cooldown_slow":
        return f"ability cooldowns are doubled for {duration}s"
    if effect == "heavy_slow":
        return f"ability cooldowns are tripled and proc chance is reduced for {duration}s"
    if effect == "proc_penalty":
        return f"their next abilities have a reduced proc chance for {duration}s"
    if effect == "ability_lock":
        return f"their abilities are locked for {duration}s"
    if effect == "typing_delay":
        return f"typing adds a short recovery delay for {duration}s"
    if effect == "mute":
        return f"they are muted for {duration}s"
    if effect == "timeout":
        return f"they are timed out for {duration}s"
    if effect == "channel_kick":
        return f"they are removed from this channel for {duration}s"
    return "the configured effect is applied"


async def temporary_channel_removal(
    channel: discord.abc.GuildChannel,
    target: discord.Member,
    duration: int,
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return
    previous = channel.overwrites_for(target)
    try:
        await channel.set_permissions(
            target,
            overwrite=discord.PermissionOverwrite(view_channel=False, send_messages=False),
            reason="Sins 2.0 target-scoped channel effect",
        )
        await asyncio.sleep(duration)
        await channel.set_permissions(target, overwrite=previous, reason="Sins 2.0 effect expired")
    except (discord.Forbidden, discord.HTTPException):
        pass


async def apply_ability_effect(
    ctx: commands.Context,
    target: Optional[discord.Member],
    effect: str,
    duration: int,
    state: dict,
) -> str:
    actor_user = get_user(state, ctx.author.id)
    if effect == "clear_self":
        actor_user["effects"].clear()
        return f"{ctx.author.mention}'s negative effects were cleared."
    if target is None:
        return "This ability needs a mentioned target."

    target_user = get_user(state, target.id)
    if effect == "cooldown_slow":
        effect_until(target_user, "cooldown_multiplier_until", duration)
        effect_until(target_user, "post_message_lock_until", duration)
    elif effect == "heavy_slow":
        effect_until(target_user, "cooldown_multiplier_until", duration)
        effect_until(target_user, "heavy_cooldown_until", duration)
        effect_until(target_user, "proc_penalty_until", duration)
    elif effect == "proc_penalty":
        effect_until(target_user, "proc_penalty_until", duration)
    elif effect == "ability_lock":
        effect_until(target_user, "ability_locked_until", duration)
    elif effect == "typing_delay":
        effect_until(target_user, "typing_delay_until", duration)
    elif effect in {"clear_target", "freedom"}:
        for key in (
            "cooldown_multiplier_until",
            "heavy_cooldown_until",
            "proc_penalty_until",
            "ability_locked_until",
            "typing_delay_until",
            "post_message_lock_until",
        ):
            target_user["effects"].pop(key, None)
        if effect == "freedom":
            try:
                await target.timeout(None, reason="Sins 2.0 freedom effect")
            except (discord.Forbidden, discord.HTTPException):
                pass
    elif effect in {"ally_haste", "self_haste"}:
        recipient = target_user if effect == "ally_haste" else actor_user
        recipient["effects"]["haste_until"] = now_ts() + duration
    elif effect == "inspect":
        active = [
            f"{key}: {display_remaining(value)}"
            for key, value in target_user["effects"].items()
            if float(value or 0) > now_ts()
        ]
        return f"{target.mention} active effects: {', '.join(active) or 'none'}."
    elif effect == "mute":
        try:
            await target.timeout(
                datetime.now(timezone.utc) + timedelta(seconds=duration),
                reason=f"Sins 2.0 ability by {ctx.author}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return "Discord refused the target timeout; check Moderate Members and role hierarchy."
    elif effect == "timeout":
        try:
            await target.timeout(
                datetime.now(timezone.utc) + timedelta(seconds=duration),
                reason=f"Sins 2.0 ability by {ctx.author}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return "Discord refused the target timeout; check Moderate Members and role hierarchy."
    elif effect == "channel_kick":
        asyncio.create_task(temporary_channel_removal(ctx.channel, target, duration))

    save_state(state)
    if effect in {"mute", "timeout"}:
        return f"{target.mention} was affected: {effect_text(effect, duration)}."
    return f"{target.mention}: {effect_text(effect, duration)}."


async def execute_ability(ctx: commands.Context, ability_name: str) -> None:
    definition = ABILITY_DEFINITIONS[ability_name]
    held = set(held_role_keys(ctx.author))
    required = definition["roles"]
    if required and not held.intersection(required):
        await ctx.send("❌ You do not hold the role required for that ability.", delete_after=8)
        return
    if not held:
        await ctx.send("❌ You need a managed role before using abilities.", delete_after=8)
        return

    state = load_state()
    user = get_user(state, ctx.author.id)
    now = now_ts()
    if float(user["effects"].get("ability_locked_until", 0) or 0) > now:
        await ctx.send(
            f"❌ Your abilities are locked for {display_remaining(user['effects']['ability_locked_until'])}.",
            delete_after=8,
        )
        return
    if float(user["effects"].get("post_message_lock_until", 0) or 0) > now:
        await ctx.send("❌ Your typing recovery delay is still active.", delete_after=6)
        return
    ready_at = float(user["cooldowns"].get(ability_name, 0) or 0)
    if ready_at > now:
        await ctx.send(f"❌ That ability is on cooldown for {display_remaining(ready_at)}.", delete_after=8)
        return
    if effect_active(user, "proc_penalty_until") and random.random() < 0.50:
        user["cooldowns"][ability_name] = now + definition["cooldown"]
        save_state(state)
        await ctx.send("❌ The proc penalty caused that ability to fail.", delete_after=8)
        return

    target = _target_from_context(ctx)
    if definition["needs_target"] and target is None:
        await ctx.send(f"❌ Mention a target for `!{ability_name}`.", delete_after=8)
        return
    if target and target.bot:
        await ctx.send("❌ Abilities cannot target bots.", delete_after=6)
        return

    multiplier = 1
    if effect_active(user, "cooldown_multiplier_until"):
        multiplier *= 2
    if effect_active(user, "heavy_cooldown_until"):
        multiplier *= 2
    if effect_active(user, "haste_until"):
        multiplier = max(1, multiplier // 2)
    user["cooldowns"][ability_name] = now + max(5, definition["cooldown"] * multiplier)
    message = await apply_ability_effect(
        ctx,
        target,
        definition["effect"],
        definition["duration"],
        state,
    )
    save_state(state)
    await ctx.send(f"✨ **{ability_name}** — {message}")


def _make_ability_command(ability_name: str):
    async def ability_command(ctx: commands.Context, *args):
        await execute_ability(ctx, ability_name)

    ability_command.__name__ = f"ability_{ability_name}"
    return ability_command


def register_ability_commands() -> None:
    for ability_name in ABILITY_DEFINITIONS:
        bot.command(name=ability_name)(_make_ability_command(ability_name))


register_ability_commands()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    state = load_state()
    user = get_user(state, message.author.id)
    if (
        not message.content.startswith(PREFIX)
        and effect_active(user, "typing_delay_until")
    ):
        user["last_message_at"] = now_ts()
        effect_until(user, "post_message_lock_until", 8)
        save_state(state)
    await bot.process_commands(message)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if before.roles != after.roles:
        await reconcile_member_roles(after)


@bot.event
async def on_ready() -> None:
    global slash_commands_synced
    if not slash_commands_synced:
        try:
            synced = await bot.tree.sync()
            slash_commands_synced = True
            print(f"Synced {len(synced)} application command(s), including /myabilities.")
        except Exception as exc:
            print(f"Could not sync application commands: {type(exc).__name__}: {exc}")
    print(f"{BOT_NAME} online as {bot.user}")


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN must be set to run Sins Bot 2.0.")
    bot.run(token)


if __name__ == "__main__":
    main()