#!/usr/bin/env python3
"""
Zetsubo 2.0 — Seven Deadly Sins Role System
============================================

A deliberately stripped-down rewrite of the original bot.

WHAT IS GONE (on purpose):
  • Trials, trial windows, trial failure/corruption, fall-from-grace
  • HP, damage, !attack, !heal, combat stats, disasters, bounties, pacts
  • Second forms / evolved modes / ultimate meters / powerup gating
  • Separate combat "paths" as a parallel system

WHAT IS KEPT:
  • Every role, its abilities, and its effects
  • One holder per role, enforced globally
  • Path abilities folded directly into each role's base moveset
  • Old trial-style obtainment kept, but strictly OPTIONAL

DESIGN RULES:
  1. No ability deals damage. Every offensive ability applies a STATUS EFFECT.
  2. Every effect is written to exactly one user id — never a role, never a
     list, never "everyone with X". This is what fixes the stray-mute bug.
  3. Nothing is gated behind a meter, a form, or a powerup. If you hold the
     role, you can use the whole kit, right now.

Entry point:  python3 seven_sins_bot.py
Env var:      DISCORD_TOKEN
Data file:    sins2_data.json
"""

import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

DATA_FILE = "sins2_data.json"
PREFIX = "!"

# Roles at or above this power need community approval to claim.
APPROVAL_POWER_THRESHOLD = 5
# How many approvals are needed, by power tier.
APPROVALS_REQUIRED = {5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5}
# How long a pending request stays open before expiring.
REQUEST_TTL = 48 * 3600

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


def now_ts() -> int:
    return int(time.time())


def remaining_fmt(ts: int) -> str:
    secs = max(0, int(ts) - now_ts())
    if secs >= 3600:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"


# ═══════════════════════════════════════════════════════════════════
# STATUS EFFECTS
# ═══════════════════════════════════════════════════════════════════
# These replace every former damage/HP mechanic. Each one degrades how a
# person can act, rather than draining a number.

# Abilities whose behaviour is too specific for the generic spec tuple.
# ability_key -> async handler(ctx, data, user, spec, target). Populated at the
# bottom of this file; declared here so run_ability can see it at call time.
CUSTOM_ABILITIES: dict = {}

# Roles hidden from !roles until their holder asks. Secret roles still work
# normally in every other respect.
SECRET_ROLES: set = set()

EFFECTS = {
    "ability_lock": {
        "name": "Ability Lock",
        "icon": "🔒",
        "desc": "Cannot use any role ability.",
    },
    "cooldown_tax": {
        "name": "Cooldown Tax",
        "icon": "⏳",
        "desc": "All of your ability cooldowns are doubled.",
    },
    "misfire": {
        "name": "Misfire",
        "icon": "🎲",
        "desc": "Each ability you use has a chance to fizzle and still go on cooldown.",
    },
    "speech_lag": {
        "name": "Speech Lag",
        "icon": "🐌",
        "desc": "After each message you send, you must wait before sending another.",
    },
    "mute": {
        "name": "Silenced",
        "icon": "🤐",
        "desc": "Your messages are removed by the bot.",
    },
    "timeout": {
        "name": "Timeout",
        "icon": "⛔",
        "desc": "A real Discord timeout.",
    },
    "channel_exile": {
        "name": "Channel Exile",
        "icon": "🚪",
        "desc": "Removed from one channel temporarily.",
    },
}


def _empty_data() -> dict:
    return {
        "users": {},
        "role_holders": {},   # role_key -> user_id (str). One holder, always.
        "requests": {},       # role_key -> {requester_id, approvals[], opened_at}
        "config": {},         # guild_id -> {"owner_grant_only": bool}
        "verity_requests": {},       # target_id -> {asker_id, opened_at}
        "mandates": {},              # guild_id:user_id -> active mandate record
        "verity_audit_log": {},      # guild_id -> append-only bot-side audit entries
        "temporary_role_grants": {}, # grant_id -> role/member/mandate ownership
        "kaleb_targets": {},         # guild_id -> target and expiry for the exclusive role
    }


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return _empty_data()
    try:
        with open(DATA_FILE, "r") as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_data()
    for k, v in _empty_data().items():
        d.setdefault(k, v)
    return d


def save_data(data: dict):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)


def get_user(data: dict, user_id) -> dict:
    uid = str(user_id)
    u = data["users"].setdefault(uid, {})
    u.setdefault("role_key", None)       # the ONE role they hold
    u.setdefault("cooldowns", {})        # ability_key -> expiry ts
    u.setdefault("effects", {})          # effect_key -> {"expires": ts, "source": uid}
    u.setdefault("last_message_ts", 0)   # for speech_lag
    u.setdefault("optional_progress", {})  # legacy trial-style obtainment (optional)
    return u


# ═══════════════════════════════════════════════════════════════════
# EFFECT ENGINE
# ═══════════════════════════════════════════════════════════════════

def has_effect(user: dict, key: str) -> bool:
    """True only if THIS user's own effect entry is live."""
    entry = user.get("effects", {}).get(key)
    if not entry:
        return False
    if now_ts() >= entry.get("expires", 0):
        return False
    return True


def clear_expired_effects(user: dict):
    live = {}
    for k, v in user.get("effects", {}).items():
        if now_ts() < v.get("expires", 0):
            live[k] = v
    user["effects"] = live


def apply_effect(data: dict, target_id: int, effect_key: str,
                 duration: int, source_id: int) -> dict:
    """
    Write an effect to EXACTLY ONE user record.

    This function is the only place effects are created. It takes a concrete
    target_id, never a role, a list, or a channel. That containment is what
    stops an ability from leaking onto bystanders — the old bug came from
    effects being applied by role lookup, which swept up anyone who happened
    to share the role at that moment.
    """
    if effect_key not in EFFECTS:
        raise ValueError(f"unknown effect {effect_key}")
    target = get_user(data, target_id)
    clear_expired_effects(target)
    existing = target["effects"].get(effect_key)
    expires = now_ts() + duration
    # Refresh rather than stack, so repeat casts can't spiral.
    if existing and existing.get("expires", 0) > expires:
        expires = existing["expires"]
    target["effects"][effect_key] = {
        "expires": expires,
        "source": str(source_id),
    }
    return target["effects"][effect_key]


def effect_summary(user: dict) -> str:
    clear_expired_effects(user)
    if not user.get("effects"):
        return "*(none)*"
    lines = []
    for k, v in user["effects"].items():
        meta = EFFECTS.get(k, {"icon": "•", "name": k})
        lines.append(f"{meta['icon']} **{meta['name']}** — {remaining_fmt(v['expires'])}")
    return "\n".join(lines)


async def apply_timeout(member: discord.Member, seconds: int, reason: str) -> bool:
    """Real Discord timeout, scoped to one member. Returns success."""
    try:
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def exile_from_channel(member: discord.Member, channel: discord.TextChannel,
                             seconds: int, reason: str) -> bool:
    """
    Temporarily deny one member access to one channel, then restore.

    Uses a per-member channel overwrite, which touches only this member. It
    never edits the role's permissions, so nobody else in the channel is
    affected.
    """
    try:
        await channel.set_permissions(member, view_channel=False,
                                      send_messages=False, reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        return False

    async def _restore():
        await asyncio.sleep(seconds)
        try:
            await channel.set_permissions(member, overwrite=None,
                                          reason="Channel exile expired")
        except Exception:
            pass

    asyncio.create_task(_restore())
    return True


# ═══════════════════════════════════════════════════════════════════
# ROLE REGISTRY
# ═══════════════════════════════════════════════════════════════════
# One flat registry. Sin roles, virtue roles, standalone virtues, and myths
# all live here on equal footing. Each role's `abilities` list is its COMPLETE
# base moveset — former second-form abilities and former path abilities have
# been folded in. Nothing here is gated behind a meter or a form.
#
# ability tuple: (key, display, category, cooldown_secs, effect, duration, needs_target)

ROLES: dict[str, dict] = {

    # ── SINS ────────────────────────────────────────────────────────
    "lust": {
        "name": "Desire Bound Lust",
        "kind": "sin",
        "power": 1,
        "color": (200, 60, 120),
        "blurb": "Charm that binds attention and refuses to let it go.",
        "abilities": [
            ("obsess", "Obsess", "control", 900, "misfire", 600, True),
            ("allure", "Allure", "control", 1200, "speech_lag", 480, True),
            ("devotion", "Devotion", "support", 1800, None, 0, False),
            ("bind", "Bind", "disrupt", 2700, "ability_lock", 420, True),
        ],
    },
    "gluttony": {
        "name": "The Devoured",
        "kind": "sin",
        "power": 2,
        "color": (160, 50, 0),
        "blurb": "An appetite that consumes speech itself.",
        "abilities": [
            ("gorge", "Gorge", "support", 900, None, 0, False),
            ("feast", "Feast", "disrupt", 1200, "speech_lag", 600, True),
            ("devour", "Devour", "silence", 3600, "mute", 420, True),
            ("purge_bite", "Purge Bite", "utility", 1200, None, 0, True),
            ("expel", "Expel", "exile", 5400, "channel_exile", 480, True),
        ],
    },
    "greed": {
        "name": "The False King",
        "kind": "sin",
        "power": 3,
        "color": (220, 180, 40),
        "blurb": "Takes what others rely on, and makes them wait to get it back.",
        "abilities": [
            ("seize", "Seize", "disrupt", 1800, "ability_lock", 480, True),
            ("tax", "Tax", "disrupt", 1500, "cooldown_tax", 900, True),
            ("hoard", "Hoard", "support", 2400, None, 0, False),
            ("extort", "Extort", "control", 2700, "misfire", 720, True),
        ],
    },
    "sloth": {
        "name": "The Vessel of Sloth",
        "kind": "sin",
        "power": 4,
        "color": (110, 110, 140),
        "blurb": "Slows everything it touches until acting stops being worth it.",
        "abilities": [
            ("slowdown", "Slowdown", "disrupt", 900, "speech_lag", 900, True),
            ("force_lazy", "Force Lazy", "disrupt", 1500, "cooldown_tax", 1200, True),
            ("deep_sleep", "Deep Sleep", "disrupt", 3600, "ability_lock", 900, True),
            ("drowse", "Drowse", "control", 2100, "misfire", 900, True),
            ("sleepwalk", "Sleepwalk", "support", 1800, None, 0, False),
        ],
    },
    "wrath": {
        "name": "Crimson Heir",
        "kind": "sin",
        "power": 5,
        "color": (200, 40, 30),
        "blurb": "Overwhelming force, expressed as sanction rather than damage.",
        "abilities": [
            ("rage_strike", "Rage Strike", "disrupt", 1200, "cooldown_tax", 900, True),
            ("bloodlust", "Bloodlust", "support", 2400, None, 0, False),
            ("meteor", "Meteor", "aoe", 7200, "ability_lock", 600, True),
            ("smite", "Smite", "silence", 3600, "mute", 300, True),
            ("shatter", "Shatter", "disrupt", 2700, "misfire", 900, True),
        ],
    },
    "envy": {
        "name": "The Pale Mirror",
        "kind": "sin",
        "power": 6,
        "color": (0, 180, 216),
        "blurb": "Copies, distorts, and unsettles. Never clashes — it simply lands.",
        "abilities": [
            ("jealousy_mark", "Jealousy Mark", "control", 1200, "misfire", 900, True),
            ("schizo", "Schizo", "control", 900, "speech_lag", 600, True),
            ("mirror", "Mirror", "support", 1800, None, 0, False),
            ("covet", "Covet", "disrupt", 2700, "ability_lock", 600, True),
            ("unmake", "Unmake", "disrupt", 3600, "cooldown_tax", 1200, True),
        ],
    },
    "pride": {
        "name": "The Bearer of Pride",
        "kind": "sin",
        "power": 7,
        "color": (230, 200, 90),
        "blurb": "Commands deference. The strongest sanctions in the kit.",
        "abilities": [
            ("claim", "Claim", "control", 1800, "speech_lag", 900, True),
            ("weaken", "Weaken", "disrupt", 2100, "cooldown_tax", 1200, True),
            ("stop_time", "Stop Time", "silence", 5400, "mute", 480, True),
            ("decree", "Decree", "exile", 7200, "channel_exile", 600, True),
            ("sovereign", "Sovereign", "support", 3600, None, 0, False),
        ],
    },

    # ── VIRTUES ─────────────────────────────────────────────────────
    "chastity": {
        "name": "The Chaste",
        "kind": "virtue",
        "power": 8,
        "color": (240, 240, 255),
        "blurb": "Clears what binds, and refuses what would bind.",
        "abilities": [
            ("abstain", "Abstain", "cleanse", 1200, None, 0, False),
            ("purify", "Purify", "cleanse", 1800, None, 0, True),
            ("ward", "Ward", "support", 2400, None, 0, False),
        ],
    },
    "temperance": {
        "name": "The Fasting King",
        "kind": "virtue",
        "power": 9,
        "color": (200, 220, 180),
        "blurb": "Moderates excess — shortens what others inflict.",
        "abilities": [
            ("fast", "Fast", "cleanse", 1200, None, 0, False),
            ("moderate", "Moderate", "cleanse", 1500, None, 0, True),
            ("restraint", "Restraint", "support", 2400, None, 0, False),
        ],
    },
    "charity": {
        "name": "The Open Hand",
        "kind": "virtue",
        "power": 10,
        "color": (255, 210, 120),
        "blurb": "Gives away relief, including its own.",
        "abilities": [
            ("gift_relief", "Gift Relief", "cleanse", 1500, None, 0, True),
            ("share_load", "Share Load", "support", 2100, None, 0, True),
            ("open_hand", "Open Hand", "cleanse", 3000, None, 0, False),
        ],
    },
    "diligence": {
        "name": "The Waking",
        "kind": "virtue",
        "power": 11,
        "color": (180, 230, 180),
        "blurb": "Refuses to slow down, and wakes others who have.",
        "abilities": [
            ("rouse", "Rouse", "cleanse", 1200, None, 0, True),
            ("inspire", "Inspire", "support", 1800, None, 0, True),
            ("persist", "Persist", "support", 2400, None, 0, False),
        ],
    },
    "patience": {
        "name": "The Still Flame",
        "kind": "virtue",
        "power": 12,
        "color": (255, 180, 120),
        "blurb": "Absorbs what is thrown, and calms what is burning.",
        "abilities": [
            ("absorb", "Absorb", "support", 1800, None, 0, False),
            ("de_escalate", "De-escalate", "cleanse", 1500, None, 0, True),
            ("endure", "Endure", "support", 2700, None, 0, False),
        ],
    },
    "kindness": {
        "name": "The Mirror's Grace",
        "kind": "virtue",
        "power": 13,
        "color": (220, 200, 255),
        "blurb": "Undoes cruelty directly.",
        "abilities": [
            ("bless", "Bless", "cleanse", 1500, None, 0, True),
            ("forgive", "Forgive", "cleanse", 2400, None, 0, True),
            ("grace", "Grace", "support", 3000, None, 0, False),
        ],
    },
    "humility": {
        "name": "The Humble Sovereign",
        "kind": "virtue",
        "power": 14,
        "color": (200, 200, 220),
        "blurb": "The strongest cleanse in the game, and it never targets itself.",
        "abilities": [
            ("submit", "Submit", "cleanse", 1800, None, 0, True),
            ("lift_up", "Lift Up", "cleanse", 2400, None, 0, True),
            ("humble", "Humble", "disrupt", 3600, "cooldown_tax", 900, True),
        ],
    },

    # ── STANDALONE VIRTUES ──────────────────────────────────────────
    "justice": {
        "name": "The Scales of Justice",
        "kind": "standalone",
        "power": 9,
        "color": (200, 180, 80),
        "blurb": "Reviews and reverses what was done to others.",
        "abilities": [
            ("verdict", "Verdict", "cleanse", 2100, None, 0, True),
            ("condemn", "Condemn", "disrupt", 2700, "ability_lock", 600, True),
            ("overrule", "Overrule", "cleanse", 3600, None, 0, True),
        ],
    },
    "prudence": {
        "name": "The Prudent Eye",
        "kind": "standalone",
        "power": 8,
        "color": (170, 200, 220),
        "blurb": "Sees what is coming and blunts it.",
        "abilities": [
            ("discern", "Discern", "utility", 900, None, 0, True),
            ("anticipate", "Anticipate", "support", 1800, None, 0, False),
            ("counsel", "Counsel", "cleanse", 2400, None, 0, True),
        ],
    },
    "fortitude": {
        "name": "The Unbroken",
        "kind": "standalone",
        "power": 9,
        "color": (190, 140, 90),
        "blurb": "Cannot be locked down for long.",
        "abilities": [
            ("iron_will", "Iron Will", "support", 1800, None, 0, False),
            ("fortify", "Fortify", "support", 2400, None, 0, True),
            ("unbreak", "Unbreak", "cleanse", 3000, None, 0, False),
        ],
    },
    "faith": {
        "name": "The Faithful",
        "kind": "standalone",
        "power": 10,
        "color": (255, 245, 200),
        "blurb": "Unreliable, but occasionally decisive.",
        "abilities": [
            ("invoke_faith", "Invoke Faith", "cleanse", 1800, None, 0, False),
            ("prayer", "Prayer", "support", 2400, None, 0, True),
            ("judgment", "Judgment", "disrupt", 3600, "misfire", 900, True),
        ],
    },
    "hope": {
        "name": "The Hopeful",
        "kind": "standalone",
        "power": 10,
        "color": (100, 180, 255),
        "blurb": "Lifts everyone it can reach.",
        "abilities": [
            ("rally", "Rally", "cleanse", 2400, None, 0, True),
            ("beacon", "Beacon", "support", 3000, None, 0, False),
            ("uplift", "Uplift", "cleanse", 1800, None, 0, True),
        ],
    },
    "liberality": {
        "name": "The Open Spirit",
        "kind": "standalone",
        "power": 8,
        "color": (180, 230, 180),
        "blurb": "Frees the restrained.",
        "abilities": [
            ("grant_freedom", "Grant Freedom", "cleanse", 2100, None, 0, True),
            ("unbind", "Unbind", "cleanse", 1500, None, 0, True),
            ("bestow", "Bestow", "support", 2700, None, 0, True),
        ],
    },

    # ── MYTHS ───────────────────────────────────────────────────────
    "la_llorona": {
        "name": "La Llorona",
        "kind": "myth",
        "power": 6,
        "color": (70, 150, 175),
        "blurb": "The Weeping Woman. Her cry silences; her river pulls under.",
        "abilities": [
            ("wail", "The Wail", "disrupt", 3600, "ability_lock", 600, True),
            ("veil", "Weeping Veil", "support", 7200, None, 0, False),
            ("lure", "River's Lure", "silence", 10800, "timeout", 60, True),
        ],
    },
}

# ability_key -> role_key, built once so lookups are O(1) and unambiguous.
ABILITY_OWNER: dict[str, str] = {}
ABILITY_SPEC: dict[str, tuple] = {}
for _rk, _rv in ROLES.items():
    for _ab in _rv["abilities"]:
        ABILITY_OWNER[_ab[0]] = _rk
        ABILITY_SPEC[_ab[0]] = _ab


def role_display(role_key: str) -> str:
    return ROLES[role_key]["name"]


def approvals_needed(role_key: str) -> int:
    power = ROLES[role_key]["power"]
    if power < APPROVAL_POWER_THRESHOLD:
        return 0
    return APPROVALS_REQUIRED.get(power, 5)


def find_role_key(query: str) -> Optional[str]:
    """Match a role by key or by display name, case-insensitively."""
    q = query.lower().strip()
    if q in ROLES:
        return q
    for k, v in ROLES.items():
        if v["name"].lower() == q:
            return k
    for k, v in ROLES.items():
        if q in v["name"].lower() or q in k:
            return k
    return None


# ═══════════════════════════════════════════════════════════════════
# DISCORD ROLE SYNC
# ═══════════════════════════════════════════════════════════════════
# The original bot recorded a role in its JSON and then *separately* tried to
# add the Discord role, swallowing failures. When the add failed (role missing,
# or the bot's role sat too low) the data said you had it but Discord didn't.
# ensure_discord_role() below creates the role if absent, verifies hierarchy,
# and reports the real reason on failure instead of failing silently.


async def ensure_discord_role(guild: discord.Guild, role_key: str) -> tuple[Optional[discord.Role], Optional[str]]:
    """Get or create the Discord role. Returns (role, error_message)."""
    spec = ROLES[role_key]
    name = spec["name"]
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        try:
            role = await guild.create_role(
                name=name,
                color=discord.Color.from_rgb(*spec["color"]),
                hoist=True,
                reason="Zetsubo 2.0 role sync",
            )
        except discord.Forbidden:
            return None, "I lack **Manage Roles** permission, so I can't create the role."
        except discord.HTTPException as e:
            return None, f"Discord rejected the role creation: `{e.status}`."
    me = guild.me
    if me is None:
        return None, "I can't read my own member record in this server."
    if role >= me.top_role:
        return None, (
            f"**{name}** sits above my highest role, so Discord won't let me assign it.\n"
            "Fix: Server Settings → Roles → drag my role above it."
        )
    return role, None


async def sync_assign(member: discord.Member, role_key: str) -> tuple[bool, str]:
    """Assign the Discord role and report honestly. Never claims success blindly."""
    role, err = await ensure_discord_role(member.guild, role_key)
    if err:
        return False, err
    if role in member.roles:
        return True, f"**{role.name}** was already on {member.mention}."
    try:
        await member.add_roles(role, reason="Zetsubo 2.0 role grant")
    except discord.Forbidden:
        return False, f"Permission denied while assigning **{role.name}**."
    except discord.HTTPException as e:
        return False, f"Discord API error assigning **{role.name}**: `{e.status}`."
    # Verify rather than assume.
    fresh = member.guild.get_member(member.id)
    if fresh and role not in fresh.roles:
        return False, f"Discord accepted the request but **{role.name}** did not stick. Check role hierarchy."
    return True, f"**{role.name}** assigned to {member.mention}."


async def sync_remove(member: discord.Member, role_key: str) -> tuple[bool, str]:
    role = discord.utils.get(member.guild.roles, name=ROLES[role_key]["name"])
    if role is None or role not in member.roles:
        return True, "Role was not present."
    try:
        await member.remove_roles(role, reason="Zetsubo 2.0 role release")
        return True, f"**{role.name}** removed."
    except discord.Forbidden:
        return False, f"Permission denied while removing **{role.name}**."
    except discord.HTTPException as e:
        return False, f"Discord API error removing **{role.name}**: `{e.status}`."


def holder_of(data: dict, role_key: str) -> Optional[str]:
    return data["role_holders"].get(role_key)


async def bind_role(data: dict, member: discord.Member, role_key: str) -> tuple[bool, str]:
    """Record + assign in one step. Enforces one-role-per-person and one-person-per-role."""
    existing_holder = holder_of(data, role_key)
    if existing_holder and existing_holder != str(member.id):
        return False, f"**{role_display(role_key)}** is already held by <@{existing_holder}>."

    user = get_user(data, member.id)
    old = user.get("role_key")
    if old and old != role_key:
        await sync_remove(member, old)
        data["role_holders"].pop(old, None)

    ok, msg = await sync_assign(member, role_key)
    if not ok:
        return False, msg

    user["role_key"] = role_key
    data["role_holders"][role_key] = str(member.id)
    data["requests"].pop(role_key, None)
    save_data(data)
    return True, msg


# ═══════════════════════════════════════════════════════════════════
# ROLE BROWSING
# ═══════════════════════════════════════════════════════════════════

@bot.command(name="roles")
async def roles_cmd(ctx, kind: str = None):
    """List every role. Optionally filter: sin | virtue | standalone | myth"""
    data = load_data()
    save_data(data)
    kinds = ["sin", "virtue", "standalone", "myth"]
    if kind and kind.lower() not in kinds:
        await ctx.send(f"Unknown category. Try: `{'` | `'.join(kinds)}`", delete_after=10)
        return

    embed = discord.Embed(
        title="📜 Roles",
        description="`!role_info <role>` for a full kit • `!request_role <role>` to claim one",
        color=discord.Color.blurple(),
    )
    for k in kinds:
        if kind and k != kind.lower():
            continue
        entries = []
        for rk, rv in sorted(ROLES.items(), key=lambda x: x[1]["power"]):
            if rv["kind"] != k:
                continue
            # Secret roles are omitted unless the viewer is the holder.
            if rk in SECRET_ROLES and holder_of(data, rk) != str(ctx.author.id):
                continue
            h = holder_of(data, rk)
            status = f"held by <@{h}>" if h else "**open**"
            gate = "🔓" if rv["power"] < APPROVAL_POWER_THRESHOLD else "🔐"
            entries.append(f"{gate} **{rv['name']}** (P{rv['power']}) — {status}")
        if entries:
            embed.add_field(name=k.capitalize(), value="\n".join(entries), inline=False)
    embed.set_footer(text="🔓 claim instantly  •  🔐 needs approvals, legacy trial, or the owner")
    await ctx.send(embed=embed)


@bot.command(name="role_info")
async def role_info(ctx, *, query: str):
    """Show a role's complete base moveset."""
    rk = find_role_key(query)
    if not rk:
        await ctx.send(f"No role matches `{query}`. Try `!roles`.", delete_after=10)
        return
    data = load_data()
    save_data(data)
    spec = ROLES[rk]
    h = holder_of(data, rk)

    embed = discord.Embed(
        title=f"{spec['name']}",
        description=f"*{spec['blurb']}*",
        color=discord.Color.from_rgb(*spec["color"]),
    )
    embed.add_field(name="Category", value=spec["kind"].capitalize(), inline=True)
    embed.add_field(name="Power", value=str(spec["power"]), inline=True)
    embed.add_field(name="Holder", value=f"<@{h}>" if h else "*open*", inline=True)

    lines = []
    for key, disp, cat, cd, eff, dur, needs_t in spec["abilities"]:
        tgt = " `@user`" if needs_t else ""
        if eff:
            meta = EFFECTS[eff]
            detail = f"{meta['icon']} {meta['name']} for {dur // 60}m"
        else:
            detail = "self/ally effect"
        lines.append(f"**`!{key}{tgt}`** — *{cat}* · {detail} · CD {cd // 60}m")
    embed.add_field(name="Full moveset (no forms, no unlocks)",
                    value="\n".join(lines), inline=False)

    need = approvals_needed(rk)
    if need:
        embed.add_field(
            name="How to obtain",
            value=(f"Power {spec['power']} is gated. Choose one:\n"
                   f"• `!request_role {rk}` then **{need}** members `!approve @you`\n"
                   f"• `!legacy_trial {rk}` — the old trial route (optional)\n"
                   f"• Ask the server owner to `!assign_role @you {rk}`"),
            inline=False,
        )
    else:
        embed.add_field(name="How to obtain",
                        value=f"Open tier — `!request_role {rk}` grants it immediately if free.",
                        inline=False)
    await ctx.send(embed=embed)


@bot.command(name="myrole")
async def myrole(ctx):
    """Your role, your kit, your cooldowns, and anything currently affecting you."""
    data = load_data()
    user = get_user(data, ctx.author.id)
    clear_expired_effects(user)
    save_data(data)

    rk = user.get("role_key")
    if not rk:
        await ctx.send("You hold no role. `!roles` to browse, `!request_role <role>` to claim.")
        return
    spec = ROLES[rk]
    embed = discord.Embed(
        title=f"{spec['name']}",
        description=f"*{spec['blurb']}*",
        color=discord.Color.from_rgb(*spec["color"]),
    )
    lines = []
    for key, disp, cat, cd, eff, dur, needs_t in spec["abilities"]:
        ready = user["cooldowns"].get(key, 0)
        mark = "✅" if now_ts() >= ready else f"⏳ {remaining_fmt(ready)}"
        tgt = " @user" if needs_t else ""
        lines.append(f"{mark} **`!{key}{tgt}`** — *{cat}*")
    embed.add_field(name="Abilities", value="\n".join(lines), inline=False)
    embed.add_field(name="Affecting you", value=effect_summary(user), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="effects")
async def effects_cmd(ctx, member: discord.Member = None):
    """See what's currently affecting you or someone else."""
    member = member or ctx.author
    data = load_data()
    user = get_user(data, member.id)
    clear_expired_effects(user)
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title=f"Status — {member.display_name}",
        description=effect_summary(user),
        color=discord.Color.greyple(),
    ))


# ═══════════════════════════════════════════════════════════════════
# OBTAINMENT
# ═══════════════════════════════════════════════════════════════════
# Three routes, all valid:
#   1. Below the power threshold  -> instant, first come first served
#   2. At/above the threshold     -> N community approvals
#   3. Any role, any time         -> server owner / admin just assigns it
# The legacy trial route is preserved but entirely optional (!legacy_trial).


@bot.command(name="request_role")
async def request_role(ctx, *, query: str):
    """Ask for a role. Low power grants instantly; high power opens an approval vote."""
    rk = find_role_key(query)
    if not rk:
        await ctx.send(f"No role matches `{query}`. Try `!roles`.", delete_after=10)
        return

    data = load_data()
    user = get_user(data, ctx.author.id)

    holder = holder_of(data, rk)
    if holder == str(ctx.author.id):
        await ctx.send("You already hold that role.", delete_after=8)
        save_data(data); return
    if holder:
        await ctx.send(
            f"**{role_display(rk)}** is taken by <@{holder}>. "
            "Only one person may hold a role at a time.", delete_after=12)
        save_data(data); return

    need = approvals_needed(rk)

    if need == 0:
        ok, msg = await bind_role(data, ctx.author, rk)
        if ok:
            await ctx.send(embed=discord.Embed(
                title="✅ Role Claimed",
                description=f"{ctx.author.mention} now holds **{role_display(rk)}**.\n{msg}\n\n"
                            f"`!myrole` to see your full kit — everything is available immediately.",
                color=discord.Color.green(),
            ))
        else:
            await ctx.send(f"❌ {msg}")
        return

    # Gated tier — open or refresh a request.
    existing = data["requests"].get(rk)
    if existing and now_ts() - existing["opened_at"] < REQUEST_TTL:
        if existing["requester_id"] != str(ctx.author.id):
            await ctx.send(
                f"<@{existing['requester_id']}> already has an open request for "
                f"**{role_display(rk)}** ({len(existing['approvals'])}/{need}).",
                delete_after=12)
            save_data(data); return
        await ctx.send(
            f"Your request is already open — **{len(existing['approvals'])}/{need}** approvals.",
            delete_after=10)
        save_data(data); return

    data["requests"][rk] = {
        "requester_id": str(ctx.author.id),
        "approvals": [],
        "opened_at": now_ts(),
    }
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🔐 Approval Requested",
        description=(
            f"{ctx.author.mention} is requesting **{role_display(rk)}** (Power {ROLES[rk]['power']}).\n\n"
            f"**{need}** other members must run `!approve {ctx.author.mention}`.\n\n"
            f"Alternatives: `!legacy_trial {rk}` for the old trial route, "
            "or an admin can simply assign it."
        ),
        color=discord.Color.gold(),
    ))


@bot.command(name="approve")
async def approve(ctx, member: discord.Member):
    """Approve someone's pending role request."""
    data = load_data()
    if member.id == ctx.author.id:
        await ctx.send("You cannot approve yourself.", delete_after=8)
        save_data(data); return

    target_rk = None
    for rk, req in data["requests"].items():
        if req["requester_id"] == str(member.id) and now_ts() - req["opened_at"] < REQUEST_TTL:
            target_rk = rk
            break
    if not target_rk:
        await ctx.send(f"{member.display_name} has no open role request.", delete_after=8)
        save_data(data); return

    req = data["requests"][target_rk]
    uid = str(ctx.author.id)
    if uid in req["approvals"]:
        await ctx.send("You already approved this request.", delete_after=8)
        save_data(data); return

    req["approvals"].append(uid)
    need = approvals_needed(target_rk)
    got = len(req["approvals"])
    save_data(data)

    if got < need:
        await ctx.send(f"👍 **{got}/{need}** approvals for {member.mention} → **{role_display(target_rk)}**.")
        return

    ok, msg = await bind_role(data, member, target_rk)
    if ok:
        await ctx.send(embed=discord.Embed(
            title="✅ Approved",
            description=(f"{member.mention} has been granted **{role_display(target_rk)}** "
                         f"with {got} approvals.\n{msg}"),
            color=discord.Color.green(),
        ))
    else:
        await ctx.send(f"❌ Approvals met, but assignment failed: {msg}")


@bot.command(name="requests")
async def requests_cmd(ctx):
    """View all open role requests."""
    data = load_data()
    save_data(data)
    live = {rk: r for rk, r in data["requests"].items()
            if now_ts() - r["opened_at"] < REQUEST_TTL}
    if not live:
        await ctx.send("No open requests.")
        return
    lines = []
    for rk, r in live.items():
        lines.append(f"**{role_display(rk)}** — <@{r['requester_id']}> "
                     f"({len(r['approvals'])}/{approvals_needed(rk)})")
    await ctx.send(embed=discord.Embed(
        title="🔐 Open Requests", description="\n".join(lines), color=discord.Color.gold()))


@bot.command(name="release_role")
async def release_role(ctx):
    """Give up your role so someone else can take it."""
    data = load_data()
    user = get_user(data, ctx.author.id)
    rk = user.get("role_key")
    if not rk:
        await ctx.send("You hold no role.", delete_after=8)
        save_data(data); return
    await sync_remove(ctx.author, rk)
    data["role_holders"].pop(rk, None)
    user["role_key"] = None
    save_data(data)
    await ctx.send(f"🕊️ {ctx.author.mention} released **{role_display(rk)}**. It is now open.")


@bot.command(name="legacy_trial")
async def legacy_trial(ctx, *, query: str):
    """
    OPTIONAL. The original trial-style obtainment, preserved for anyone who
    prefers earning a role the old way instead of using approvals.
    """
    rk = find_role_key(query)
    if not rk:
        await ctx.send(f"No role matches `{query}`. Try `!roles`.", delete_after=10)
        return
    data = load_data()
    user = get_user(data, ctx.author.id)
    if holder_of(data, rk):
        await ctx.send(f"**{role_display(rk)}** is already held.", delete_after=10)
        save_data(data); return

    user["optional_progress"][rk] = {"started": now_ts()}
    save_data(data)

    legacy = LEGACY_TRIALS.get(rk, "Demonstrate the role's nature publicly, then ask an admin to confirm.")
    await ctx.send(embed=discord.Embed(
        title=f"📜 Legacy Trial — {role_display(rk)}",
        description=(
            f"{legacy}\n\n"
            "**This route is optional and admin-judged.** When you believe you've met it, "
            f"ask an admin to run `!assign_role {ctx.author.mention} {rk}`.\n\n"
            f"You can abandon this at any time and use `!request_role {rk}` instead."
        ),
        color=discord.Color.dark_gold(),
    ))


LEGACY_TRIALS = {
    "lust":      "Collect 5 unique ❤️ reactions from different members within 24 hours.",
    "gluttony":  "React to every message in the feast channel within 5 minutes, for 24 hours.",
    "greed":     "Silence someone without being exposed, within 8 hours.",
    "sloth":     "Abbreviate every word longer than 4 characters, for 48 hours.",
    "wrath":     "Include a curse word in every message you send, for 24 hours.",
    "envy":      "Strip a role from a member without being exposed, within 12 hours.",
    "pride":     "Proclaim your superiority and collect 10 unique 🙏 reactions within 48 hours.",
    "chastity":  "Add no ❤️ reaction to anything for 48 hours.",
    "temperance":"Add no reactions at all for 24 hours.",
    "charity":   "Give a role you hold to another member, admin-confirmed.",
    "diligence": "Post a 20+ word message every hour for 24 consecutive hours.",
    "patience":  "Send no curse words for 48 hours.",
    "kindness":  "Genuinely praise 5 different members, 10+ words each, within 24 hours.",
    "humility":  "Publicly elevate others above yourself and collect 10 🙏 reactions in 48 hours.",
}


# ═══════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════

@bot.command(name="assign_role")
@commands.has_permissions(administrator=True)
async def assign_role(ctx, member: discord.Member, *, query: str):
    """Admin/owner: grant any role directly, bypassing approvals entirely."""
    rk = find_role_key(query)
    if not rk:
        await ctx.send(f"No role matches `{query}`. Try `!roles`.", delete_after=10)
        return
    data = load_data()
    holder = holder_of(data, rk)
    if holder and holder != str(member.id):
        await ctx.send(
            f"⚠️ **{role_display(rk)}** is held by <@{holder}>. "
            f"Run `!revoke_role <@{holder}>` first, or they can `!release_role`.",
            delete_after=15)
        save_data(data); return
    ok, msg = await bind_role(data, member, rk)
    await ctx.send(("✅ " if ok else "❌ ") + msg)


@bot.command(name="revoke_role")
@commands.has_permissions(administrator=True)
async def revoke_role(ctx, member: discord.Member):
    """Admin: strip a member's role and reopen it."""
    data = load_data()
    user = get_user(data, member.id)
    rk = user.get("role_key")
    if not rk:
        await ctx.send(f"{member.display_name} holds no role.", delete_after=8)
        save_data(data); return
    ok, msg = await sync_remove(member, rk)
    data["role_holders"].pop(rk, None)
    user["role_key"] = None
    save_data(data)
    await ctx.send(f"🔓 **{role_display(rk)}** revoked from {member.mention} and reopened. {msg}")


@bot.command(name="clear_effects")
@commands.has_permissions(administrator=True)
async def clear_effects(ctx, member: discord.Member):
    """Admin: wipe every status effect from one member. Useful if something ever sticks."""
    data = load_data()
    user = get_user(data, member.id)
    count = len(user.get("effects", {}))
    user["effects"] = {}
    save_data(data)
    try:
        await member.timeout(None, reason=f"!clear_effects by {ctx.author}")
    except Exception:
        pass
    await ctx.send(f"🧹 Cleared **{count}** effect(s) from {member.mention}, including any Discord timeout.")


@bot.command(name="sync_roles")
@commands.has_permissions(administrator=True)
async def sync_roles(ctx):
    """
    Admin: reconcile the data file against Discord.

    Fixes the classic failure where the bot recorded a role but Discord never
    actually applied it (bot role too low, role deleted, member left, etc).
    """
    data = load_data()
    fixed, broken, orphaned = [], [], []

    for rk, uid in list(data["role_holders"].items()):
        member = ctx.guild.get_member(int(uid))
        if member is None:
            data["role_holders"].pop(rk, None)
            u = data["users"].get(uid)
            if u:
                u["role_key"] = None
            orphaned.append(f"{role_display(rk)} (member gone)")
            continue
        role = discord.utils.get(ctx.guild.roles, name=ROLES[rk]["name"])
        if role is None or role not in member.roles:
            ok, msg = await sync_assign(member, rk)
            (fixed if ok else broken).append(f"{role_display(rk)} → {member.display_name}: {msg}")
    save_data(data)

    embed = discord.Embed(title="🔄 Role Sync", color=discord.Color.blurple())
    embed.add_field(name=f"Repaired ({len(fixed)})",
                    value="\n".join(fixed)[:1000] or "*none*", inline=False)
    if broken:
        embed.add_field(name=f"Failed ({len(broken)})",
                        value="\n".join(broken)[:1000], inline=False)
    if orphaned:
        embed.add_field(name=f"Cleaned up ({len(orphaned)})",
                        value="\n".join(orphaned)[:1000], inline=False)
    await ctx.send(embed=embed)


@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup_cmd(ctx):
    """Admin: create every role defined in the registry. Safe to re-run."""
    created, existed, failed = [], [], []
    for rk in ROLES:
        pre_existing = discord.utils.get(ctx.guild.roles, name=ROLES[rk]["name"]) is not None
        role, err = await ensure_discord_role(ctx.guild, rk)
        if err:
            failed.append(f"{ROLES[rk]['name']}: {err}")
        elif role:
            (existed if pre_existing else created).append(role.name)
    mandate = discord.utils.get(ctx.guild.roles, name=MANDATE_ROLE_NAME)
    if mandate is None:
        try:
            mandate = await ctx.guild.create_role(
                name=MANDATE_ROLE_NAME,
                color=discord.Color.from_rgb(255, 215, 0),
                hoist=True,
                permissions=discord.Permissions(
                    manage_messages=True,
                    moderate_members=True,
                    manage_nicknames=True,
                    mute_members=True,
                    move_members=True,
                ),
                reason="Zetsubo 2.0 setup — safe Verity Mandate",
            )
            created.append(MANDATE_ROLE_NAME)
        except discord.Forbidden:
            failed.append(f"{MANDATE_ROLE_NAME}: missing Manage Roles permission")
        except discord.HTTPException as exc:
            failed.append(f"{MANDATE_ROLE_NAME}: Discord {exc.status}")
    else:
        existed.append(MANDATE_ROLE_NAME)
    log_channel = discord.utils.get(ctx.guild.text_channels, name=VERITY_AUDIT_CHANNEL_NAME)
    if log_channel is None:
        try:
            await ctx.guild.create_text_channel(
                VERITY_AUDIT_CHANNEL_NAME,
                overwrites={
                    ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    ctx.guild.me: discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    ),
                },
                reason="Zetsubo 2.0 setup — Verity audit log",
            )
            created.append(f"#{VERITY_AUDIT_CHANNEL_NAME}")
        except discord.Forbidden:
            failed.append(f"#{VERITY_AUDIT_CHANNEL_NAME}: missing Manage Channels permission")
        except discord.HTTPException as exc:
            failed.append(f"#{VERITY_AUDIT_CHANNEL_NAME}: Discord {exc.status}")
    else:
        existed.append(f"#{VERITY_AUDIT_CHANNEL_NAME}")
    embed = discord.Embed(
        title="⚙️ Setup Complete",
        description="Creates the registry roles, safe Mandate role, and private Mandate audit channel.",
        color=discord.Color.green(),
    )
    if created:
        embed.add_field(name=f"Created ({len(created)})", value=", ".join(created)[:1000], inline=False)
    if existed:
        embed.add_field(name=f"Already present ({len(existed)})", value=", ".join(existed)[:1000], inline=False)
    if failed:
        embed.add_field(name=f"Problems ({len(failed)})", value="\n".join(failed)[:1000], inline=False)
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════════
# ABILITY ENGINE
# ═══════════════════════════════════════════════════════════════════
# Every ability in the game routes through run_ability(). There is exactly one
# code path that applies an effect, and it always receives a single resolved
# discord.Member. Nothing here ever iterates over a role's member list, which
# is precisely why a cast can no longer splash onto bystanders.

CLEANSE_ABILITIES = {
    # ability_key -> (how many effects to strip, whether it targets others)
    "abstain":       (2, False),
    "purify":        (3, True),
    "fast":          (2, False),
    "moderate":      (2, True),
    "gift_relief":   (3, True),
    "open_hand":     (4, False),
    "rouse":         (2, True),
    "de_escalate":   (2, True),
    "bless":         (3, True),
    "forgive":       (4, True),
    "submit":        (3, True),
    "lift_up":       (4, True),
    "verdict":       (3, True),
    "overrule":      (5, True),
    "counsel":       (2, True),
    "unbreak":       (3, False),
    "invoke_faith":  (2, False),
    "rally":         (3, True),
    "uplift":        (2, True),
    "grant_freedom": (3, True),
    "unbind":        (2, True),
}

# Self/ally buffs that grant temporary protection rather than stripping.
WARD_ABILITIES = {
    "devotion", "gorge", "hoard", "sleepwalk", "bloodlust", "mirror",
    "sovereign", "ward", "restraint", "persist", "absorb", "endure",
    "grace", "anticipate", "iron_will", "fortify", "beacon", "veil",
    "share_load", "inspire", "prayer", "bestow",
}


def ward_active(user: dict) -> bool:
    return now_ts() < (user.get("ward_until") or 0)


def effective_cooldown(user: dict, base: int) -> int:
    """Cooldown Tax doubles cooldowns. This is the only cooldown modifier."""
    return base * 2 if has_effect(user, "cooldown_tax") else base


async def run_ability(ctx, ability_key: str, target: Optional[discord.Member]):
    """Single entry point for every ability in the game."""
    data = load_data()
    user = get_user(data, ctx.author.id)
    clear_expired_effects(user)

    spec = ABILITY_SPEC[ability_key]
    key, disp, cat, base_cd, effect, duration, needs_target = spec
    owner_role = ABILITY_OWNER[ability_key]

    # 1. Must hold the role. No forms, no meters, no unlock conditions.
    # Exception: Lord Verity's "Brain Eaten" grants temporary use of a consumed kit.
    if user.get("role_key") != owner_role:
        kit = user.get("devoured_kit")
        borrowed = bool(kit and now_ts() < kit.get("expires", 0)
                        and kit.get("role_key") == owner_role)
        kaleb_copy = user.get("kaleb_copy")
        copied = bool(
            kaleb_copy
            and now_ts() < kaleb_copy.get("expires", 0)
            and ability_key in kaleb_copy.get("abilities", [])
        )
        if not borrowed and not copied:
            await ctx.send(
                f"🚫 `!{key}` belongs to **{role_display(owner_role)}**, which you don't hold.",
                delete_after=10)
            save_data(data); return

    # 2. Ability Lock blocks everything.
    if has_effect(user, "ability_lock"):
        e = user["effects"]["ability_lock"]
        await ctx.send(f"🔒 You are ability-locked for {remaining_fmt(e['expires'])}.", delete_after=10)
        save_data(data); return

    # 3. Cooldown.
    ready = user["cooldowns"].get(key, 0)
    if now_ts() < ready:
        await ctx.send(f"⏳ **{disp}** is on cooldown — {remaining_fmt(ready)}.", delete_after=8)
        save_data(data); return

    # 4. Target resolution.
    if needs_target:
        if target is None:
            await ctx.send(f"`!{key} @user` — this ability needs a target.", delete_after=10)
            save_data(data); return
        if target.bot:
            await ctx.send("You can't target a bot.", delete_after=8)
            save_data(data); return
        if target.id == ctx.author.id and cat not in ("cleanse", "support"):
            await ctx.send("You can't target yourself with that.", delete_after=8)
            save_data(data); return

    cd = effective_cooldown(user, base_cd)
    await _record_mandate_action(
        data,
        ctx.author,
        f"ability:{key}",
        target=target,
        details=f"Used **{disp}** through the safe Mandate window.",
    )

    # 4b. Custom-logic abilities take over here, after all the standard gating
    # (role held, not ability-locked, off cooldown, target resolved) has passed.
    if key in CUSTOM_ABILITIES:
        user["cooldowns"][key] = now_ts() + cd
        save_data(data)
        await CUSTOM_ABILITIES[key](ctx, data, user, spec, target)
        return

    # 5. Misfire — costs the cooldown, produces nothing.
    if has_effect(user, "misfire") and random.random() < 0.40:
        user["cooldowns"][key] = now_ts() + cd
        save_data(data)
        await ctx.send(embed=discord.Embed(
            title=f"🎲 {disp} — Misfired",
            description=f"{ctx.author.mention}'s **{disp}** fizzles out. Cooldown still applies.",
            color=discord.Color.dark_grey(),
        ))
        return

    user["cooldowns"][key] = now_ts() + cd

    # ── CLEANSE ────────────────────────────────────────────────────
    if key in CLEANSE_ABILITIES:
        strip_count, targets_others = CLEANSE_ABILITIES[key]
        recipient = target if (targets_others and target) else ctx.author
        r_user = get_user(data, recipient.id)
        clear_expired_effects(r_user)

        removed = []
        for _ in range(strip_count):
            if not r_user["effects"]:
                break
            # Remove the longest-remaining effect first.
            worst = max(r_user["effects"].items(), key=lambda kv: kv[1]["expires"])
            removed.append(EFFECTS[worst[0]]["name"])
            r_user["effects"].pop(worst[0])

        if "timeout" in [e.lower() for e in removed] or not removed:
            try:
                await recipient.timeout(None, reason=f"!{key} by {ctx.author}")
            except Exception:
                pass

        save_data(data)
        await ctx.send(embed=discord.Embed(
            title=f"✨ {disp}",
            description=(
                f"{ctx.author.mention} uses **{disp}** on {recipient.mention}.\n\n"
                + (f"Cleared: {', '.join(removed)}" if removed
                   else "Nothing was afflicting them — the effort is spent regardless.")
            ),
            color=discord.Color.from_rgb(*ROLES[owner_role]["color"]),
        ))
        return

    # ── WARD / SELF-BUFF ───────────────────────────────────────────
    if key in WARD_ABILITIES:
        ward_len = 600
        recipient = target if (target and needs_target) else ctx.author
        r_user = get_user(data, recipient.id)
        r_user["ward_until"] = now_ts() + ward_len
        save_data(data)
        await ctx.send(embed=discord.Embed(
            title=f"🛡️ {disp}",
            description=(f"{recipient.mention} is warded for **{ward_len // 60} minutes** — "
                         "the next hostile effect aimed at them is refused."),
            color=discord.Color.from_rgb(*ROLES[owner_role]["color"]),
        ))
        return

    # ── UTILITY ────────────────────────────────────────────────────
    if key == "purge_bite":
        deleted = 0
        try:
            async for msg in ctx.channel.history(limit=150):
                if msg.author.id == target.id:
                    try:
                        await msg.delete()
                        deleted += 1
                    except Exception:
                        pass
                    if deleted >= 3:
                        break
        except Exception:
            pass
        save_data(data)
        await ctx.send(f"🍽️ **Purge Bite** — removed **{deleted}** of {target.mention}'s recent messages.")
        return

    if key == "discern":
        t_user = get_user(data, target.id)
        clear_expired_effects(t_user)
        save_data(data)
        t_role = t_user.get("role_key")
        await ctx.send(embed=discord.Embed(
            title=f"🔍 Discern — {target.display_name}",
            description=(f"**Role:** {role_display(t_role) if t_role else '*none*'}\n"
                         f"**Warded:** {'yes' if ward_active(t_user) else 'no'}\n\n"
                         f"**Effects:**\n{effect_summary(t_user)}"),
            color=discord.Color.from_rgb(*ROLES[owner_role]["color"]),
        ))
        return

    # ── HOSTILE EFFECT ─────────────────────────────────────────────
    t_user = get_user(data, target.id)
    clear_expired_effects(t_user)

    # "I Don't Give A Damn" — blanket immunity, beats everything.
    if _is_immune(t_user):
        save_data(data)
        await ctx.send(embed=discord.Embed(
            title=f"😐 {disp} — Ignored",
            description=f"{target.mention} does not give a damn. Nothing happens.",
            color=discord.Color.light_grey(),
        ))
        return

    # "Ow!" — reflect the effect back at the caster.
    counter = t_user.get("counter_armed")
    if counter and now_ts() < counter.get("expires", 0):
        t_user.pop("counter_armed", None)
        if not _is_immune(user):
            apply_effect(data, ctx.author.id, effect, duration, target.id)
        save_data(data)
        meta_r = EFFECTS[effect]
        await ctx.send(embed=discord.Embed(
            title=f"😤 Ow! — Countered",
            description=(
                f"{target.mention} takes **{disp}** on the chin and fires back.\n\n"
                f"{ctx.author.mention} suffers {meta_r['icon']} **{meta_r['name']}** instead."
            ),
            color=discord.Color.from_rgb(240, 230, 140),
        ))
        return

    if ward_active(t_user):
        t_user["ward_until"] = 0
        save_data(data)
        await ctx.send(embed=discord.Embed(
            title=f"🛡️ {disp} — Refused",
            description=f"{target.mention}'s ward absorbs **{disp}** and breaks.",
            color=discord.Color.light_grey(),
        ))
        return

    meta = EFFECTS[effect]
    note = ""

    if effect == "timeout":
        ok = await apply_timeout(target, duration, f"!{key} by {ctx.author}")
        if not ok:
            # Graceful downgrade — never silently do nothing.
            effect, duration = "mute", max(duration, 300)
            meta = EFFECTS[effect]
            note = "\n*(Discord timeout unavailable — applied a bot-side silence instead.)*"

    if effect == "channel_exile":
        ok = await exile_from_channel(target, ctx.channel, duration, f"!{key} by {ctx.author}")
        if not ok:
            effect, duration = "mute", duration
            meta = EFFECTS[effect]
            note = "\n*(Channel permissions unavailable — applied a bot-side silence instead.)*"
        else:
            note = f"\n*(Removed from #{ctx.channel.name} only.)*"

    # THE single write. One user id, nothing else.
    apply_effect(data, target.id, effect, duration, ctx.author.id)
    save_data(data)

    await ctx.send(embed=discord.Embed(
        title=f"{meta['icon']} {disp}",
        description=(
            f"{ctx.author.mention} uses **{disp}** on {target.mention}.\n\n"
            f"{meta['icon']} **{meta['name']}** for **{duration // 60}m "
            f"{duration % 60}s** — {meta['desc']}{note}"
        ),
        color=discord.Color.from_rgb(*ROLES[owner_role]["color"]),
    ))


def _make_ability_command(ability_key: str):
    """Register one bot command per ability, all routed through run_ability."""
    key, disp, cat, cd, eff, dur, needs_target = ABILITY_SPEC[ability_key]
    owner = ABILITY_OWNER[ability_key]

    if needs_target:
        async def _cmd(ctx, target: discord.Member = None):
            await run_ability(ctx, ability_key, target)
    else:
        async def _cmd(ctx):
            await run_ability(ctx, ability_key, None)

    _cmd.__name__ = f"ability_{ability_key}"
    _cmd.__doc__ = f"({ROLES[owner]['name']}) {disp} — {cat}."
    bot.command(name=key)(_cmd)


for _key in ABILITY_SPEC:
    _make_ability_command(_key)


# ═══════════════════════════════════════════════════════════════════
# MESSAGE ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════
# Enforcement reads ONLY the message author's own record. There is no role
# lookup and no member iteration here, so a person can never be silenced by
# an effect that was aimed at someone else.

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    data = load_data()
    user = get_user(data, message.author.id)
    clear_expired_effects(user)
    mandate = _active_mandate(data, message.author)
    if mandate:
        entry = _audit_entry(
            data,
            message.guild,
            message.author,
            "message",
            details=(
                f"#{message.channel.name}: {message.content[:700]}"
                + (f" [attachments: {', '.join(a.filename for a in message.attachments)[:200]}]"
                   if message.attachments else "")
            ),
            mandate_id=mandate.get("mandate_id"),
        )
        mandate.setdefault("actions", []).append(entry)
        save_data(data)
        await _post_audit(message.guild, entry)

    # Expansion mechanics (Lord Verity obtainment, compulsions, Burger passives).
    try:
        if await _expansion_hooks(message, data, user):
            return
    except Exception:
        pass
    data = load_data()
    user = get_user(data, message.author.id)
    clear_expired_effects(user)

    # Immunity ignores every enforcement below it.
    if _is_immune(user):
        user["last_message_ts"] = now_ts()
        save_data(data)
        await bot.process_commands(message)
        return

    # Silenced — remove the message. Their own effect entry, nobody else's.
    if has_effect(user, "mute"):
        expires = user["effects"]["mute"]["expires"]
        try:
            await message.delete()
        except Exception:
            pass
        save_data(data)
        try:
            await message.author.send(
                f"🤐 You're silenced for another {remaining_fmt(expires)} — that message was removed."
            )
        except Exception:
            pass
        return

    # Grey-Blue partial speech — only a short fragment is allowed through.
    if now_ts() < (user.get("partial_speech_until") or 0) and not message.content.startswith("!"):
        words = message.content.split()
        if len(words) > 3:
            fragment = " ".join(words[:2])
            try:
                await message.delete()
            except Exception:
                pass
            save_data(data)
            try:
                await message.channel.send(f"🔘 {message.author.display_name} (fragment): {fragment}", delete_after=20)
            except Exception:
                pass
            return

    # Speech Lag — enforce a gap between messages.
    if has_effect(user, "speech_lag"):
        gap = 20
        since = now_ts() - user.get("last_message_ts", 0)
        if since < gap:
            try:
                await message.delete()
            except Exception:
                pass
            save_data(data)
            try:
                await message.author.send(
                    f"🐌 Speech Lag — wait **{gap - since}s** more before your next message."
                )
            except Exception:
                pass
            return

    user["last_message_ts"] = now_ts()
    save_data(data)
    await bot.process_commands(message)


@tasks.loop(minutes=2)
async def effect_janitor():
    """Prune expired effects so the data file never accumulates stale entries."""
    data = load_data()
    touched = False
    for uid, u in data.get("users", {}).items():
        before = len(u.get("effects", {}))
        clear_expired_effects(u)
        if len(u.get("effects", {})) != before:
            touched = True
    # Expire stale role requests too.
    for rk in list(data.get("requests", {})):
        if now_ts() - data["requests"][rk]["opened_at"] >= REQUEST_TTL:
            data["requests"].pop(rk)
            touched = True
    if touched:
        save_data(data)


# ═══════════════════════════════════════════════════════════════════
# HELP
# ═══════════════════════════════════════════════════════════════════

@bot.command(name="commands", aliases=["help", "guide"])
async def commands_cmd(ctx):
    """Everything this bot does."""
    e = discord.Embed(
        title="Zetsubo 2.0 — Commands",
        description=(
            "A role-and-abilities bot. **No trials, no HP, no combat, no forms.**\n"
            "Hold a role, use its whole kit immediately."
        ),
        color=discord.Color.blurple(),
    )
    e.add_field(
        name="Roles",
        value=("`!roles [sin|virtue|standalone|myth]` — browse\n"
               "`!role_info <role>` — full moveset\n"
               "`!myrole` — your kit + cooldowns + status\n"
               "`!effects [@user]` — what's affecting someone"),
        inline=False,
    )
    e.add_field(
        name="Getting a role",
        value=(f"`!request_role <role>` — instant below power {APPROVAL_POWER_THRESHOLD}, "
               "otherwise opens an approval vote\n"
               "`!approve @user` — back someone's request\n"
               "`!requests` — see open requests\n"
               "`!release_role` — give yours up\n"
               "`!legacy_trial <role>` — *optional* old-style trial route"),
        inline=False,
    )
    e.add_field(
        name="Admin",
        value=("`!setup` — create all roles\n"
               "`!assign_role @user <role>` — grant anything directly\n"
               "`!revoke_role @user` — strip and reopen\n"
               "`!clear_effects @user` — wipe all status effects\n"
               "`!sync_roles` — repair data/Discord mismatches"),
        inline=False,
    )
    e.add_field(
        name="Status effects (these replaced all damage)",
        value="\n".join(f"{v['icon']} **{v['name']}** — {v['desc']}" for v in EFFECTS.values()),
        inline=False,
    )
    e.set_footer(text=f"{len(ROLES)} roles · {len(ABILITY_SPEC)} abilities · one holder per role")
    await ctx.send(embed=e)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 That command is administrator-only.", delete_after=8)
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("Couldn't find that member — @mention them directly.", delete_after=8)
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. See `!commands`.", delete_after=10)
        return
    raise error


# ═══════════════════════════════════════════════════════════════════
# ░░ EXPANSION ROLES ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
# Lord Verity, Burger, and Fart Boy. Added on top of the original 21
# roles, which are untouched.
# ═══════════════════════════════════════════════════════════════════

VERITY_ROLE_KEY = "lord_verity"
BURGER_ROLE_KEY = "burger"
FARTBOY_ROLE_KEY = "fart_boy"
KALEB_ROLE_KEY = "kaleb_band_kid"

# The elevated stand-in for real Administrator. See MANDATE_NOTE.
MANDATE_ROLE_NAME = "Verity's Mandate"
MANDATE_DURATION = 5 * 60
MANDATE_COOLDOWN = 30 * 60
VERITY_AUDIT_CHANNEL_NAME = "zetsubo-admin-log"
KALEB_USERNAME = "kalebgoon"

MANDATE_NOTE = (
    "**Verity's Mandate is not real Discord Administrator.** Administrator bypasses "
    "every permission check in Discord, so it cannot be meaningfully time-limited — "
    "anyone holding it, even briefly, could grant themselves permanent roles, ban, or "
    "delete channels, and no bot could take that back. The Mandate instead grants "
    "elevated *bot-side* authority: message management, effect application, and "
    "temporary role grants that this bot itself reverses when the timer ends. "
    "No banning. No kicking. Nothing permanent."
)

ROLES.update({
    VERITY_ROLE_KEY: {
        "name": "Lord Verity",
        "kind": "sovereign",
        "power": 15,
        "color": (240, 230, 140),
        "blurb": "Demands recognition. Rules by decree, counter, and sheer contempt.",
        "abilities": [
            ("kneel",            "Kneel",                     "control",  900,  None, 0, True),
            ("stop",             "Stop",                      "control",  600,  None, 0, True),
            ("ow",               "Ow!",                       "counter",  300,  None, 0, False),
            ("brain_eaten",      "Brain Eaten",               "steal",    3600, None, 0, True),
            ("nooo",             "NOOO!",                     "silence",  1200, None, 0, True),
            ("your_last_day",    "YOUR LAST DAY!",            "erase",    5400, None, 0, True),
            ("i_dont_give_a_damn", "I Don't Give A Damn",     "counter",  1800, None, 0, False),
            ("master_of_humanity", "Master Of Humanity",      "control",  1500, None, 0, True),
            ("i_am_the_master",  "I Am The Master Of Humanity", "mandate", 1800, None, 0, False),
            ("you_dont_deserve_your_brain", "You Don't Deserve Your Brain At All", "special", 0, None, 0, False),
        ],
    },
    BURGER_ROLE_KEY: {
        "name": "Burger",
        "kind": "secret",
        "power": 6,
        "color": (210, 140, 60),
        "blurb": "Nobody talks about Burger. Burger talks about supernatural.",
        "abilities": [
            ("side_of_fries",  "I'll Have A Side Of Fries With That", "disrupt", 900,  "speech_lag", 300, True),
            ("self_glaze",     "Self Glaze",                          "cleanse", 1200, None, 0, False),
            ("i_should_leave", "I Should Just Leave",                 "utility", 1800, None, 0, False),
            ("castiel",        "Turn Somebody Into Castiel",          "control", 2700, None, 0, True),
            ("be_yourself",    "Just Be Yourself",                    "support", 1500, None, 0, False),
        ],
    },
    FARTBOY_ROLE_KEY: {
        "name": "Fart Boy",
        "kind": "secret",
        "power": 7,
        "color": (150, 170, 60),
        "blurb": "A one-man atmospheric hazard.",
        "abilities": [
            ("fart",          "Fart",          "silence", 60,   "timeout", 20, True),
            ("rapid_farts",   "Rapid Farts",   "aoe",     900,  None, 0, False),
            ("fart_beatbox",  "Fart Beatbox",  "aoe",     1800, None, 0, False),
            ("fart_nuke",     "Fart Nuke",     "aoe",     3660, None, 0, False),
        ],
    },
    KALEB_ROLE_KEY: {
        "name": "Kaleb Obsessed Band Kid",
        "kind": "special",
        "power": 8,
        "color": (190, 80, 180),
        "blurb": "A username-bound obsession role. Its abilities only work for kalebgoon.",
        "abilities": [
            ("kaleb_obsess", "Obsess", "control", 600, "speech_lag", 120, True),
            ("kaleb_pawn", "Make Them a Pawn", "control", 1800, "ability_lock", 300, True),
            ("kaleb_copy", "Access Their Powers", "steal", 3600, None, 0, True),
            ("kaleb_grant_role", "Grant a Temporary Role", "support", 1800, None, 0, True),
            ("kaleb_kiss", "Make Them Kiss You", "control", 1200, None, 0, True),
            ("kaleb_dog", "Turn Pawn Into a Dog", "control", 1500, None, 0, True),
        ],
    },
    "green_purple_girl": {
        "name": "Green Purple Girl",
        "kind": "special",
        "power": 4,
        "color": (90, 180, 90),
        "blurb": "Green mutes, purple picks an effect, grey-blue marks speech, record opens shame, milk carton makes replies pass out.",
        "abilities": [
            ("green", "Green", "silence", 900, "mute", 180, True),
            ("purple", "Purple", "control", 600, None, 0, True),
            ("grey_blue", "Grey-Blue", "disrupt", 900, "speech_lag", 300, True),
            ("record", "Record", "control", 1800, None, 0, True),
            ("milk_carton", "Milk Carton", "disrupt", 1200, None, 0, True),
        ],
    },
})

SECRET_ROLES.update({BURGER_ROLE_KEY, FARTBOY_ROLE_KEY})

# Rebuild the lookup tables now that new roles exist.
for _rk, _rv in ROLES.items():
    for _ab in _rv["abilities"]:
        ABILITY_OWNER[_ab[0]] = _rk
        ABILITY_SPEC[_ab[0]] = _ab


def _mandate_key(guild_id: int, member_id: int) -> str:
    return f"{guild_id}:{member_id}"


def _active_mandate(data: dict, member: discord.Member) -> Optional[dict]:
    record = data.setdefault("mandates", {}).get(_mandate_key(member.guild.id, member.id))
    if not record or now_ts() >= record.get("expires", 0):
        return None
    mandate_role = discord.utils.get(member.guild.roles, name=MANDATE_ROLE_NAME)
    if not mandate_role or mandate_role not in member.roles:
        return None
    return record


def _audit_entry(
    data: dict,
    guild: discord.Guild,
    actor: discord.Member,
    action: str,
    *,
    target: Optional[discord.Member] = None,
    details: str = "",
    mandate_id: Optional[str] = None,
    reversed_action: bool = False,
) -> dict:
    """Persist an append-only record of every action taken under a Mandate."""
    entry = {
        "ts": now_ts(),
        "actor_id": str(actor.id),
        "actor_name": str(actor),
        "action": action,
        "target_id": str(target.id) if target else None,
        "target_name": str(target) if target else None,
        "details": details[:1000],
        "mandate_id": mandate_id,
        "reversed": reversed_action,
    }
    entries = data.setdefault("verity_audit_log", {}).setdefault(str(guild.id), [])
    entries.append(entry)
    # Keep the JSON record bounded while retaining a long operational history.
    if len(entries) > 5000:
        del entries[:-5000]
    return entry


async def _post_audit(guild: discord.Guild, entry: dict):
    """Write the audit record to the private server channel when available."""
    channel = discord.utils.get(guild.text_channels, name=VERITY_AUDIT_CHANNEL_NAME)
    if channel is None:
        return
    target = f" → <@{entry['target_id']}>" if entry.get("target_id") else ""
    status = " (reversed)" if entry.get("reversed") else ""
    text = (
        f"**{entry['action']}**{status} by <@{entry['actor_id']}>{target}\n"
        f"{entry.get('details') or 'No additional details.'}\n"
        f"<t:{entry['ts']}:F>"
    )
    try:
        await channel.send(text)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _record_mandate_action(
    data: dict,
    actor: discord.Member,
    action: str,
    *,
    target: Optional[discord.Member] = None,
    details: str = "",
):
    record = _active_mandate(data, actor)
    if not record:
        return
    entry = _audit_entry(
        data,
        actor.guild,
        actor,
        action,
        target=target,
        details=details,
        mandate_id=record.get("mandate_id"),
    )
    record.setdefault("actions", []).append(entry)
    save_data(data)
    await _post_audit(actor.guild, entry)


async def _remove_mandate_grants(guild: discord.Guild, record: dict, data: dict, reason: str):
    """Remove only roles that this specific Mandate granted and that were not pre-existing."""
    actor_id = int(record.get("member_id", 0))
    actor = guild.get_member(actor_id)
    for grant in list(record.get("roles_granted", [])):
        member = guild.get_member(int(grant.get("member_id", 0)))
        role = guild.get_role(int(grant.get("role_id", 0)))
        if not member or not role:
            continue
        if grant.get("pre_existing"):
            continue
        try:
            await member.remove_roles(role, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            continue
        if actor:
            entry = _audit_entry(
                data,
                guild,
                actor,
                "temporary_role_removed",
                target=member,
                details=f"Role **{role.name}** removed: {reason}.",
                mandate_id=record.get("mandate_id"),
                reversed_action=True,
            )
            await _post_audit(guild, entry)


async def _end_mandate(guild: discord.Guild, member: discord.Member, reason: str):
    data = load_data()
    key = _mandate_key(guild.id, member.id)
    record = data.setdefault("mandates", {}).pop(key, None)
    if not record:
        return
    mandate_role = discord.utils.get(guild.roles, name=MANDATE_ROLE_NAME)
    if mandate_role and mandate_role in member.roles and not record.get("mandate_role_pre_existing"):
        try:
            await member.remove_roles(mandate_role, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass
    await _remove_mandate_grants(guild, record, data, reason)
    entry = _audit_entry(
        data,
        guild,
        member,
        "mandate_access_ended",
        details=f"Mandate access ended: {reason}. Only its own temporary grants were reversed.",
        mandate_id=record.get("mandate_id"),
        reversed_action=True,
    )
    save_data(data)
    await _post_audit(guild, entry)


def _c(ctx, role_key):
    return discord.Color.from_rgb(*ROLES[role_key]["color"])


# ═══════════════════════════════════════════════════════════════════
# LORD VERITY — obtainment
# ═══════════════════════════════════════════════════════════════════
# No command needed to ASK. Saying "call me lord verity" while @mentioning
# someone opens the request automatically. A command IS needed to accept,
# so nobody is enrolled by accident.

VERITY_TRIGGERS = (
    "call me lord verity",
    "call me lord verity!",
    "would you call me lord verity",
    "can you call me lord verity",
)


async def _handle_verity_request(message: discord.Message, data: dict) -> bool:
    low = message.content.lower()
    if not any(t in low for t in VERITY_TRIGGERS):
        return False
    if not message.mentions:
        return False
    asker = message.author
    witness = next((m for m in message.mentions if m.id != asker.id and not m.bot), None)
    if witness is None:
        return False
    if holder_of(data, VERITY_ROLE_KEY):
        await message.channel.send(
            f"👑 **Lord Verity** already has a bearer. There can be only one.",
            delete_after=12)
        return True
    data.setdefault("verity_requests", {})[str(witness.id)] = {
        "asker_id": str(asker.id),
        "opened_at": now_ts(),
    }
    save_data(data)
    await message.channel.send(embed=discord.Embed(
        title="👑 A Title Is Demanded",
        description=(
            f"{asker.mention} asks {witness.mention} to call them **Lord Verity**.\n\n"
            f"{witness.mention} — run `!accept_verity` to grant it, or ignore this "
            "and it lapses in an hour."
        ),
        color=discord.Color.from_rgb(240, 230, 140),
    ))
    return True


@bot.command(name="accept_verity")
async def accept_verity(ctx):
    """Confirm that you're calling someone Lord Verity. Only you can do this."""
    data = load_data()
    req = data.get("verity_requests", {}).get(str(ctx.author.id))
    if not req or now_ts() - req["opened_at"] > 3600:
        await ctx.send("Nobody has asked you to call them Lord Verity.", delete_after=8)
        save_data(data); return
    asker = ctx.guild.get_member(int(req["asker_id"]))
    if asker is None:
        data["verity_requests"].pop(str(ctx.author.id), None)
        save_data(data)
        await ctx.send("That person is no longer in the server.", delete_after=8)
        return
    if holder_of(data, VERITY_ROLE_KEY):
        await ctx.send("**Lord Verity** already has a bearer.", delete_after=8)
        save_data(data); return

    ok, msg = await bind_role(data, asker, VERITY_ROLE_KEY)
    data.get("verity_requests", {}).pop(str(ctx.author.id), None)
    save_data(data)
    if ok:
        await ctx.send(embed=discord.Embed(
            title="👑 Lord Verity",
            description=(f"{ctx.author.mention} kneels. {asker.mention} is now **Lord Verity**.\n{msg}\n\n"
                         "`!myrole` for the full decree."),
            color=discord.Color.from_rgb(240, 230, 140),
        ))
    else:
        await ctx.send(f"❌ {msg}")


# ═══════════════════════════════════════════════════════════════════
# LORD VERITY — ability handlers
# ═══════════════════════════════════════════════════════════════════

async def _ab_kneel(ctx, data, user, spec, target):
    """Force the target to address Verity as the master of humanity."""
    t = get_user(data, target.id)
    t["compelled_phrase"] = {
        "phrase": "master of humanity",
        "expires": now_ts() + 300,
        "by": str(ctx.author.id),
        "punish": "mute",
        "punish_secs": 30,
    }
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="👑 Kneel",
        description=(
            f"{target.mention} is compelled. Their **next message** must contain "
            f"**\"master of humanity\"**.\n\n"
            "Fail, and they are silenced for 30 seconds. They have 5 minutes."
        ),
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_stop(ctx, data, user, spec, target):
    """10 seconds of silence, or type backwards."""
    t = get_user(data, target.id)
    t["stop_order"] = {
        "silent_until": now_ts() + 10,
        "backwards_until": now_ts() + 120,
        "by": str(ctx.author.id),
    }
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="✋ Stop",
        description=(
            f"{target.mention} — **stop talking for 10 seconds.**\n\n"
            "Speak before then and you must type **backwards** for the next 2 minutes. "
            "The bot will delete anything that isn't."
        ),
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_ow(ctx, data, user, spec, target):
    """Arms a counter — the next hostile effect is reflected back."""
    user["counter_armed"] = {"expires": now_ts() + 300, "kind": "ow"}
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="😤 Ow!",
        description=(
            f"{ctx.author.mention} is braced. The **next hostile effect** aimed at them "
            "within 5 minutes is reflected straight back at the caster."
        ),
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_brain_eaten(ctx, data, user, spec, target):
    """Consume a target's role kit and wield it."""
    t = get_user(data, target.id)
    t_role = t.get("role_key")
    if not t_role:
        await ctx.send(f"{target.mention} holds no role — there's nothing in there.", delete_after=10)
        return
    user["devoured_kit"] = {
        "role_key": t_role,
        "expires": now_ts() + 1800,
        "victim": str(target.id),
    }
    t["effects"]["ability_lock"] = {"expires": now_ts() + 600, "source": str(ctx.author.id)}
    save_data(data)
    kit = ", ".join(f"`!{a[0]}`" for a in ROLES[t_role]["abilities"])
    await ctx.send(embed=discord.Embed(
        title="🧠 Brain Eaten",
        description=(
            f"{ctx.author.mention} consumes {target.mention}'s mind.\n\n"
            f"For **30 minutes** Verity may use the full **{role_display(t_role)}** kit:\n{kit}\n\n"
            f"{target.mention} is ability-locked for 10 minutes."
        ),
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_nooo(ctx, data, user, spec, target):
    """Duration scales with the number of O's typed. Deliberately undocumented."""
    raw = ctx.message.content.lower()
    os_count = 0
    idx = raw.find("no")
    if idx != -1:
        for ch in raw[idx + 1:]:
            if ch == "o":
                os_count += 1
            else:
                break
    # 2 O's -> 15s, scaling to 60s at 10+ O's.
    os_count = max(2, min(10, os_count))
    secs = int(15 + (os_count - 2) * (45 / 8))

    apply_effect(data, target.id, "mute", secs, ctx.author.id)
    save_data(data)
    voice_note = ""
    if target.voice:
        try:
            await target.edit(mute=True, reason=f"!nooo by {ctx.author}")
            voice_note = " They are muted in voice as well."

            async def _unmute_voice():
                await asyncio.sleep(secs)
                try:
                    await target.edit(mute=False, reason="NOOO! expired")
                except Exception:
                    pass
            asyncio.create_task(_unmute_voice())
        except Exception:
            pass

    # The scaling rule is intentionally not revealed here.
    await ctx.send(embed=discord.Embed(
        title="🗣️ NOOO!",
        description=f"{target.mention} is cut off.{voice_note}",
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_your_last_day(ctx, data, user, spec, target):
    """Erase the target's recent history and their near future."""
    uses = user.setdefault("last_day_uses", 0) + 1
    user["last_day_uses"] = uses
    future_mins = max(1, min(5, uses))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=10)
    deleted = 0
    status = await ctx.send("💀 *Erasing...*")
    for channel in ctx.guild.text_channels:
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.read_message_history and perms.manage_messages):
            continue
        try:
            # Bounded scan: Discord rate limits make a true 10h sweep of a busy
            # server impractical, so each channel is capped.
            async for msg in channel.history(limit=400, after=cutoff):
                if msg.author.id == target.id:
                    try:
                        await msg.delete()
                        deleted += 1
                    except Exception:
                        pass
        except Exception:
            continue

    apply_effect(data, target.id, "mute", future_mins * 60, ctx.author.id)
    save_data(data)
    await status.edit(content=None, embed=discord.Embed(
        title="💀 YOUR LAST DAY!",
        description=(
            f"{target.mention} has been **deleted**.\n\n"
            f"• **{deleted}** messages from the last 10 hours erased\n"
            f"• Everything they say for the next **{future_mins} minute(s)** is erased on sight\n\n"
            "*Their role is untouched. They simply leave no trace.*"
        ),
        color=discord.Color.dark_red(),
    ))


async def _ab_i_dont_give_a_damn(ctx, data, user, spec, target):
    """Blanket immunity, including to AoE. Can be extended to others."""
    user["immunity"] = {"expires": now_ts() + 600, "aoe_exempt": True}
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="😐 I Don't Give A Damn",
        description=(
            f"{ctx.author.mention} is **immune to every effect** for 10 minutes — "
            "including channel-wide and AoE effects. If a room gets frozen, they are "
            "the one who still talks.\n\n"
            "Extend it with `!spare @user`, or `!spare everyone`."
        ),
        color=_c(ctx, VERITY_ROLE_KEY),
    ))


async def _ab_master_of_humanity(ctx, data, user, spec, target):
    """Private compulsion — only the target learns what they must say."""
    try:
        await ctx.message.delete()
    except Exception:
        pass
    parts = ctx.message.content.split(None, 2)
    phrase = parts[2].strip() if len(parts) > 2 else "Lord Verity is the master of humanity"

    t = get_user(data, target.id)
    t["compelled_phrase"] = {
        "phrase": phrase.lower(),
        "expires": now_ts() + 300,
        "by": str(ctx.author.id),
        "punish": "mute",
        "punish_secs": 30,
    }
    save_data(data)
    try:
        await target.send(
            f"👁️ **A voice only you can hear.**\n\n"
            f"Say this, exactly, in the server within 5 minutes:\n\n> {phrase}\n\n"
            "Refuse, and you will be silenced for 30 seconds. "
            "Nobody else has been told this."
        )
        await ctx.send("👁️ *Something passes between two people. The message is already gone.*",
                       delete_after=8)
    except discord.Forbidden:
        await ctx.send(
            f"👁️ {target.mention} — check your DMs. (Couldn't reach you; open DMs from server members.)",
            delete_after=10)


async def _ab_i_am_the_master(ctx, data, user, spec, target):
    """Grant the time-boxed Mandate — a safe stand-in for Administrator."""
    recipient = target or ctx.author
    guild = ctx.guild
    if recipient.guild_permissions.administrator or recipient.id == guild.owner_id:
        await ctx.send(
            "❌ This safe Mandate is not applied to the server owner or anyone who "
            "already has real Administrator permission.",
            delete_after=12,
        )
        await _record_mandate_action(
            data,
            ctx.author,
            "mandate_denied",
            target=recipient,
            details="Recipient already has real Administrator permission or owns the server.",
        )
        return

    role = discord.utils.get(guild.roles, name=MANDATE_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=MANDATE_ROLE_NAME,
                color=discord.Color.from_rgb(255, 215, 0),
                hoist=True,
                permissions=discord.Permissions(
                    manage_messages=True,
                    moderate_members=True,
                    manage_nicknames=True,
                    mute_members=True,
                    move_members=True,
                    # Deliberately absent: administrator, ban_members,
                    # kick_members, manage_guild, manage_roles, manage_channels.
                ),
                reason="Verity's Mandate",
            )
        except discord.Forbidden:
            await ctx.send("❌ I need **Manage Roles** to create the Mandate.", delete_after=10)
            return

    if role >= guild.me.top_role:
        await ctx.send("❌ The Mandate role sits above mine — drag my role higher.", delete_after=12)
        return

    mandate_key = _mandate_key(guild.id, recipient.id)
    old_record = data.setdefault("mandates", {}).get(mandate_key)
    if old_record and now_ts() < old_record.get("expires", 0):
        await ctx.send(
            f"⏳ {recipient.mention} already has an active Mandate for "
            f"{remaining_fmt(old_record['expires'])}.",
            delete_after=10,
        )
        return

    role_pre_existing = role in recipient.roles
    try:
        if not role_pre_existing:
            await recipient.add_roles(role, reason="Verity's Mandate (5 min)")
    except Exception as e:
        await ctx.send(f"❌ Could not grant the Mandate: {type(e).__name__}", delete_after=10)
        return

    record = {
        "mandate_id": f"{guild.id}:{recipient.id}:{now_ts()}",
        "guild_id": str(guild.id),
        "member_id": str(recipient.id),
        "expires": now_ts() + MANDATE_DURATION,
        "granted_by": str(ctx.author.id),
        "roles_granted": [],
        "actions": [],
        "mandate_role_pre_existing": role_pre_existing,
    }
    data.setdefault("mandates", {})[mandate_key] = record
    entry = _audit_entry(
        data,
        guild,
        ctx.author,
        "mandate_granted",
        target=recipient,
        details=(
            f"Safe role **{MANDATE_ROLE_NAME}** active for 5 minutes. "
            "Real Administrator, ban, kick, server management, role management, "
            "channel management, and bot installation are unavailable."
        ),
        mandate_id=record["mandate_id"],
    )
    save_data(data)
    await _post_audit(guild, entry)

    await ctx.send(embed=discord.Embed(
        title="👑 I AM THE MASTER OF HUMANITY",
        description=(
            f"{recipient.mention} holds **{MANDATE_ROLE_NAME}** for **5 minutes**.\n\n"
            "**Granted:** manage messages, timeout members, manage nicknames, "
            "voice mute/move.\n"
            "**Withheld:** ban, kick, manage server, manage roles, manage channels.\n\n"
            "Anything they grant during this window is revoked when it ends. "
            "Timeouts they issue are cleared too. Nothing survives.\n\n"
            f"*{MANDATE_NOTE}*"
        ),
        color=discord.Color.gold(),
    ))

    async def _revoke():
        await asyncio.sleep(MANDATE_DURATION)
        d2 = load_data()
        live = d2.get("mandates", {}).get(mandate_key)
        if not live or live.get("mandate_id") != record["mandate_id"]:
            return
        d2["mandates"].pop(mandate_key, None)
        if not role_pre_existing:
            try:
                await recipient.remove_roles(role, reason="Mandate expired")
            except Exception:
                pass
        await _remove_mandate_grants(guild, live, d2, "Mandate expired")
        end_entry = _audit_entry(
            d2,
            guild,
            ctx.author,
            "mandate_expired",
            target=recipient,
            details="Mandate access ended; only roles granted by this Mandate were reversed.",
            mandate_id=record["mandate_id"],
            reversed_action=True,
        )
        save_data(d2)
        await _post_audit(guild, end_entry)
        try:
            await ctx.channel.send(
                f"⌛ {recipient.mention}'s Mandate has ended. "
                "Only roles granted by that Mandate were reversed.")
        except Exception:
            pass

    asyncio.create_task(_revoke())


@bot.command(name="verity_grant_role")
async def verity_grant_role(ctx, member: discord.Member, *, role_spec: str):
    """Grant one safe, temporary role while the caller's Mandate is active."""
    data = load_data()
    record = _active_mandate(data, ctx.author)
    if not record:
        await ctx.send("🚫 This command requires an active Verity's Mandate.", delete_after=8)
        return
    if member.guild_permissions.administrator or member.id == ctx.guild.owner_id:
        await ctx.send("❌ Mandate role grants cannot alter the server owner or a real administrator.", delete_after=10)
        return

    match = re.match(r"^(.*?)(?:\s+(\d+))?$", role_spec.strip())
    role_name = (match.group(1) if match else role_spec).strip()
    seconds = min(300, max(10, int(match.group(2) or 300))) if match else 300
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role is None:
        await ctx.send(f"❌ I could not find an existing role named **{role_name}**.", delete_after=10)
        return
    dangerous = (
        role.permissions.administrator
        or role.permissions.ban_members
        or role.permissions.kick_members
        or role.permissions.manage_guild
        or role.permissions.manage_roles
        or role.permissions.manage_channels
        or role.permissions.manage_webhooks
        or role.permissions.manage_expressions
        or role.permissions.manage_events
    )
    if role.is_default() or role.managed or dangerous:
        await ctx.send(
            "❌ That role is protected or can change server administration. "
            "Mandate grants are limited to safe, existing roles.",
            delete_after=10,
        )
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("❌ That role is above my highest role, so Discord will not let me grant it.", delete_after=10)
        return

    pre_existing = role in member.roles
    if not pre_existing:
        try:
            await member.add_roles(role, reason=f"Verity's Mandate by {ctx.author}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await ctx.send(f"❌ Discord rejected the temporary role grant: `{type(exc).__name__}`.", delete_after=10)
            return

    grant = {
        "member_id": str(member.id),
        "role_id": str(role.id),
        "role_name": role.name,
        "granted_at": now_ts(),
        "expires": now_ts() + seconds,
        "pre_existing": pre_existing,
    }
    record.setdefault("roles_granted", []).append(grant)
    entry = _audit_entry(
        data,
        ctx.guild,
        ctx.author,
        "temporary_role_granted",
        target=member,
        details=(
            f"Role **{role.name}** for {seconds}s. "
            + ("The member already had this role, so it will not be removed." if pre_existing
               else "It will be removed when this Mandate ends or access is lost.")
        ),
        mandate_id=record.get("mandate_id"),
    )
    save_data(data)
    await _post_audit(ctx.guild, entry)
    await ctx.send(
        f"✅ **{role.name}** {'confirmed on' if pre_existing else 'granted to'} "
        f"{member.mention} for up to **{seconds}s**. Only this Mandate's new grant is reversible.",
    )


@bot.command(name="verity_audit")
@commands.has_permissions(administrator=True)
async def verity_audit(ctx, limit: int = 20):
    """View recent Mandate actions; the JSON file remains the complete record."""
    data = load_data()
    entries = data.get("verity_audit_log", {}).get(str(ctx.guild.id), [])[-max(1, min(limit, 50)):]
    if not entries:
        await ctx.send("No Verity Mandate actions are recorded for this server.", delete_after=10)
        return
    lines = []
    for entry in entries:
        target = f" → <@{entry['target_id']}>" if entry.get("target_id") else ""
        status = " [reversed]" if entry.get("reversed") else ""
        lines.append(
            f"<t:{entry['ts']}:R> **{entry['action']}** by <@{entry['actor_id']}>{target}{status}\n"
            f"{entry.get('details', '')[:180]}"
        )
    await ctx.send(embed=discord.Embed(
        title="📜 Zetsubo Mandate Audit",
        description="\n\n".join(lines)[:3900],
        color=discord.Color.gold(),
    ))


async def _ab_you_dont_deserve_your_brain(ctx, data, user, spec, target):
    """Does nothing except resign the role. Exactly as specified."""
    await sync_remove(ctx.author, VERITY_ROLE_KEY)
    data["role_holders"].pop(VERITY_ROLE_KEY, None)
    user["role_key"] = None
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🚶 You Don't Deserve Your Brain At All",
        description=(
            f"{ctx.author.mention} looks around the server, considers the company, "
            "and walks away.\n\n"
            "**Lord Verity is vacant.** Somebody will have to ask again."
        ),
        color=discord.Color.dark_grey(),
    ))


@bot.command(name="spare")
async def spare(ctx, *, who: str = None):
    """(Lord Verity) Extend 'I Don't Give A Damn' immunity to someone, or everyone."""
    data = load_data()
    user = get_user(data, ctx.author.id)
    if user.get("role_key") != VERITY_ROLE_KEY:
        await ctx.send("Only Lord Verity may spare anyone.", delete_after=8)
        save_data(data); return
    if not user.get("immunity") or now_ts() >= user["immunity"].get("expires", 0):
        await ctx.send("Use `!i_dont_give_a_damn` first.", delete_after=8)
        save_data(data); return

    until = user["immunity"]["expires"]
    if who and who.lower() in ("everyone", "all", "@everyone"):
        for m in ctx.guild.members:
            if not m.bot:
                get_user(data, m.id)["immunity"] = {"expires": until, "aoe_exempt": True}
        save_data(data)
        await ctx.send("😐 **Nobody** gives a damn. Everyone is immune until Verity's window closes.")
        return

    if not ctx.message.mentions:
        await ctx.send("`!spare @user` or `!spare everyone`.", delete_after=8)
        save_data(data); return
    for m in ctx.message.mentions:
        get_user(data, m.id)["immunity"] = {"expires": until, "aoe_exempt": True}
    save_data(data)
    names = ", ".join(m.mention for m in ctx.message.mentions)
    await ctx.send(f"😐 {names} — spared. Immune until Verity's window closes.")


CUSTOM_ABILITIES.update({
    "kneel": _ab_kneel,
    "stop": _ab_stop,
    "ow": _ab_ow,
    "brain_eaten": _ab_brain_eaten,
    "nooo": _ab_nooo,
    "your_last_day": _ab_your_last_day,
    "i_dont_give_a_damn": _ab_i_dont_give_a_damn,
    "master_of_humanity": _ab_master_of_humanity,
    "i_am_the_master": _ab_i_am_the_master,
    "you_dont_deserve_your_brain": _ab_you_dont_deserve_your_brain,
})


# ═══════════════════════════════════════════════════════════════════
# BURGER
# ═══════════════════════════════════════════════════════════════════
# Obtained by reacting 🤤 or ☹️ to anything. Instant, first come only.
# Hidden from !roles until its holder looks.

BURGER_EMOJI = {"🤤", "☹️", "☹"}
SUPERNATURAL_WORDS = (
    "supernatural", "castiel", "dean winchester", "sam winchester",
    "crowley", "angel", "demon", "ghost", "wendigo", "leviathan",
)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
    emoji = str(payload.emoji)
    if emoji not in BURGER_EMOJI:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None or member.bot:
        return

    data = load_data()
    if holder_of(data, BURGER_ROLE_KEY):
        save_data(data)
        return
    ok, msg = await bind_role(data, member, BURGER_ROLE_KEY)
    save_data(data)
    if ok:
        try:
            await member.send(
                "🍔 Something happened.\n\n"
                "You have been given a role. It is not on any list. "
                "Run `!myrole` if you want to know what you are now."
            )
        except Exception:
            pass


class _BurgerDeleteView(discord.ui.View):
    """The Burger nerf — anyone may delete the message."""

    def __init__(self, message_id: int):
        super().__init__(timeout=600)
        self.message_id = message_id

    @discord.ui.button(label="Delete this", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_it(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.send_message("Couldn't delete it.", ephemeral=True)
            return
        try:
            await interaction.followup.send("🍔 Gone.", ephemeral=True)
        except Exception:
            pass


async def _ab_side_of_fries(ctx, data, user, spec, target):
    pass  # handled generically (speech_lag); kept out of CUSTOM_ABILITIES


async def _ab_self_glaze(ctx, data, user, spec, target):
    """Clear every effect on yourself, including a real timeout."""
    cleared = list(user.get("effects", {}).keys())
    user["effects"] = {}
    save_data(data)
    try:
        await ctx.author.timeout(None, reason="!self_glaze")
    except Exception:
        pass
    try:
        if ctx.author.voice:
            await ctx.author.edit(mute=False, reason="!self_glaze")
    except Exception:
        pass
    names = ", ".join(EFFECTS[c]["name"] for c in cleared if c in EFFECTS)
    await ctx.send(embed=discord.Embed(
        title="✨ Self Glaze",
        description=(f"{ctx.author.mention} glazes themselves. "
                     + (f"Cleared: {names}." if names else "There was nothing on them. Still worth it.")),
        color=_c(ctx, BURGER_ROLE_KEY),
    ))


async def _ab_i_should_leave(ctx, data, user, spec, target):
    """Voluntarily exile yourself from this channel for 2 minutes."""
    ok = await exile_from_channel(ctx.author, ctx.channel, 120, "!i_should_leave")
    save_data(data)
    if ok:
        await ctx.send(f"🚪 **{ctx.author.display_name}** has removed themselves from "
                       f"#{ctx.channel.name} for 2 minutes. Respect it.")
    else:
        await ctx.send("❌ I can't edit this channel's permissions.", delete_after=8)


async def _ab_castiel(ctx, data, user, spec, target):
    """Turn someone into Castiel — they may apply one effect to anyone."""
    t = get_user(data, target.id)
    t["is_castiel"] = {"expires": now_ts() + 900, "by": str(ctx.author.id), "used": False}
    save_data(data)
    try:
        await target.edit(nick=f"Castiel ({target.display_name})"[:32], reason="!castiel")
    except Exception:
        pass
    await ctx.send(embed=discord.Embed(
        title="🪽 Turn Somebody Into Castiel",
        description=(
            f"{target.mention} is now **Castiel** for 15 minutes.\n\n"
            "They may use `!castiel_smite @user <effect>` **once** to apply any effect "
            "in the game to anyone.\n"
            f"Effects: {', '.join(EFFECTS.keys())}"
        ),
        color=_c(ctx, BURGER_ROLE_KEY),
    ))


@bot.command(name="castiel_smite")
async def castiel_smite(ctx, target: discord.Member, effect: str):
    """(Castiel only) Apply any one effect to anyone. One use."""
    data = load_data()
    user = get_user(data, ctx.author.id)
    cas = user.get("is_castiel")
    if not cas or now_ts() >= cas.get("expires", 0):
        await ctx.send("You are not Castiel.", delete_after=8)
        save_data(data); return
    if cas.get("used"):
        await ctx.send("You've already used your one smite.", delete_after=8)
        save_data(data); return
    effect = effect.lower()
    if effect not in EFFECTS:
        await ctx.send(f"Unknown effect. Options: {', '.join(EFFECTS.keys())}", delete_after=12)
        save_data(data); return

    cas["used"] = True
    if effect == "timeout":
        await apply_timeout(target, 120, f"!castiel_smite by {ctx.author}")
    apply_effect(data, target.id, effect, 300, ctx.author.id)
    save_data(data)
    meta = EFFECTS[effect]
    await ctx.send(embed=discord.Embed(
        title="🪽 Castiel Smites",
        description=f"{ctx.author.mention} lays **{meta['name']}** on {target.mention} for 5 minutes.",
        color=discord.Color.from_rgb(120, 170, 220),
    ))


async def _ab_be_yourself(ctx, data, user, spec, target):
    user["ward_until"] = now_ts() + 900
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🍔 Just Be Yourself",
        description=(f"{ctx.author.mention} simply is.\n\n"
                     "Warded for 15 minutes — the next hostile effect is refused."),
        color=_c(ctx, BURGER_ROLE_KEY),
    ))


CUSTOM_ABILITIES.update({
    "self_glaze": _ab_self_glaze,
    "i_should_leave": _ab_i_should_leave,
    "castiel": _ab_castiel,
    "be_yourself": _ab_be_yourself,
})


# ═══════════════════════════════════════════════════════════════════
# FART BOY
# ═══════════════════════════════════════════════════════════════════

async def _ab_rapid_farts(ctx, data, user, spec, target):
    """Hit several people; each is coin-flipped between voice mute and silence."""
    pool = [m for m in ctx.guild.members
            if not m.bot and m.id != ctx.author.id][:80]
    if not pool:
        await ctx.send("Nobody to fart on.", delete_after=8)
        return
    victims = random.sample(pool, min(len(pool), random.randint(3, 5)))
    lines = []
    for v in victims:
        vu = get_user(data, v.id)
        if _is_immune(vu):
            lines.append(f"• {v.mention} — *unbothered*")
            continue
        if random.random() < 0.5 and v.voice:
            try:
                await v.edit(mute=True, reason="!rapid_farts")
                lines.append(f"• {v.mention} — 🔇 voice muted 45s")

                async def _un(m=v):
                    await asyncio.sleep(45)
                    try:
                        await m.edit(mute=False, reason="Farts cleared")
                    except Exception:
                        pass
                asyncio.create_task(_un())
            except Exception:
                apply_effect(data, v.id, "mute", 45, ctx.author.id)
                lines.append(f"• {v.mention} — 🤐 silenced 45s")
        else:
            apply_effect(data, v.id, "mute", 45, ctx.author.id)
            lines.append(f"• {v.mention} — 🤐 silenced 45s")
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="💨💨 Rapid Farts",
        description=f"{ctx.author.mention} lets loose.\n\n" + "\n".join(lines),
        color=_c(ctx, FARTBOY_ROLE_KEY),
    ))


async def _ab_fart_beatbox(ctx, data, user, spec, target):
    """Random victims get a random effect each."""
    pool = [m for m in ctx.guild.members if not m.bot and m.id != ctx.author.id][:80]
    if not pool:
        await ctx.send("Nobody to consume.", delete_after=8)
        return
    victims = random.sample(pool, min(len(pool), random.randint(2, 4)))
    choices = ["speech_lag", "mute", "misfire", "cooldown_tax"]
    lines = []
    for v in victims:
        if _is_immune(get_user(data, v.id)):
            lines.append(f"• {v.mention} — *unbothered*")
            continue
        eff = random.choice(choices)
        apply_effect(data, v.id, eff, 120, ctx.author.id)
        lines.append(f"• {v.mention} — {EFFECTS[eff]['icon']} {EFFECTS[eff]['name']} 2m")
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🎵💨 Fart Beatbox",
        description=f"{ctx.author.mention} drops a set.\n\n" + "\n".join(lines),
        color=_c(ctx, FARTBOY_ROLE_KEY),
    ))


async def _ab_fart_nuke(ctx, data, user, spec, target):
    """Every effect, on everyone, for 5 minutes. Respects immunity."""
    hit, spared = 0, 0
    for m in ctx.guild.members:
        if m.bot or m.id == ctx.author.id:
            continue
        mu = get_user(data, m.id)
        if _is_immune(mu):
            spared += 1
            continue
        for eff in ("ability_lock", "cooldown_tax", "misfire", "speech_lag", "mute"):
            apply_effect(data, m.id, eff, 300, ctx.author.id)
        hit += 1
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="☢️💨 FART NUKE",
        description=(
            f"{ctx.author.mention} ends the server.\n\n"
            f"**{hit}** members hit with **every effect in the game** for 5 minutes."
            + (f"\n**{spared}** were immune and are still talking." if spared else "")
        ),
        color=discord.Color.dark_green(),
    ))


def _kaleb_allowed(ctx) -> bool:
    return ctx.author.name.casefold() == KALEB_USERNAME.casefold()


FILTHY_DOG_ROLE = "Filthy Dog"
SHAME_ROLE = "SHAME"
DOG_ALLOWED = {"arf", "bark", "ruff", "grrr"}
DOG_COMMANDS = {"bark", "chase", "bite", "lick"}
DEGRADING_WORDS = (
    "you loser", "loser", "dork", "ew", "idiot", "nerd", "cringe",
    "pathetic", "stupid", "lame", "gross", "shut up", "weirdo", "dummy", "ratio",
)


def _kaleb_refuse(ctx) -> bool:
    return not _kaleb_allowed(ctx)


def _pawn_live(t_user: dict, owner_id: int) -> bool:
    pawn = t_user.get("kaleb_pawned_by") or {}
    return pawn.get("by") == str(owner_id) and now_ts() < pawn.get("expires", 0)


async def _ensure_named_role(guild: discord.Guild, name: str, color: tuple[int, int, int]):
    role = discord.utils.get(guild.roles, name=name)
    if role:
        return role
    try:
        return await guild.create_role(
            name=name,
            color=discord.Color.from_rgb(*color),
            reason="Zetsubo temporary roleplay role",
        )
    except (discord.Forbidden, discord.HTTPException):
        return None


async def _ab_kaleb_obsess(ctx, data, user, spec, target):
    if _kaleb_refuse(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    if _is_immune(get_user(data, target.id)):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    t_user = get_user(data, target.id)
    t_user["kaleb_obsessed_until"] = now_ts() + 600
    apply_effect(data, target.id, "mute", 20, ctx.author.id)
    apply_effect(data, target.id, "speech_lag", 180, ctx.author.id)
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="💜 Obsess",
        description=(
            f"{target.mention} is being watched.\n\n"
            "🤐 Silenced for **20 seconds**, then slower messages for **3 minutes**."
        ),
        color=_c(ctx, KALEB_ROLE_KEY),
    ))


async def _ab_kaleb_pawn(ctx, data, user, spec, target):
    if _kaleb_refuse(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    if _is_immune(get_user(data, target.id)):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    t_user = get_user(data, target.id)
    t_user["kaleb_pawned_by"] = {
        "by": str(ctx.author.id),
        "expires": now_ts() + 600,
        "order": None,
    }
    apply_effect(data, target.id, "ability_lock", 600, ctx.author.id)
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🎸 Pawn",
        description=(
            f"{target.mention} is a pawn for **10 minutes**. Abilities locked.\n\n"
            f"`!kaleb_order {target.mention} <phrase>` — they must say it, or they are muted.\n"
            f"`!kaleb_kiss {target.mention}` — forced kiss, then they throw up and pass out.\n"
            f"`!kaleb_dog {target.mention}` — Filthy Dog sub-role."
        ),
        color=_c(ctx, KALEB_ROLE_KEY),
    ))


async def _ab_kaleb_kiss(ctx, data, user, spec, target):
    if _kaleb_refuse(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    t_user = get_user(data, target.id)
    if _is_immune(t_user) or not _pawn_live(t_user, ctx.author.id):
        await ctx.send("They must be your active pawn and not protected by immunity.", delete_after=8)
        return
    apply_effect(data, target.id, "mute", 45, ctx.author.id)
    apply_effect(data, target.id, "ability_lock", 60, ctx.author.id)
    save_data(data)
    passed = await apply_timeout(target, 60, "Pawn passed out after a forced kiss")
    await ctx.send(embed=discord.Embed(
        title="💋 Forced kiss",
        description=(
            f"{target.mention} is made to kiss {ctx.author.mention}. "
            "They did not want to. They throw up, then pass out for **1 minute**."
            + ("" if passed else "\n*(Discord timeout unavailable — bot silence used instead.)*")
        ),
        color=_c(ctx, KALEB_ROLE_KEY),
    ))


async def _ab_kaleb_dog(ctx, data, user, spec, target):
    if _kaleb_refuse(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    t_user = get_user(data, target.id)
    if _is_immune(t_user) or not _pawn_live(t_user, ctx.author.id):
        await ctx.send("They must be your active pawn and not protected by immunity.", delete_after=8)
        return
    role = await _ensure_named_role(ctx.guild, FILTHY_DOG_ROLE, (120, 80, 40))
    if role is None:
        await ctx.send("I could not create the Filthy Dog role. Check Manage Roles and hierarchy.", delete_after=10)
        return
    try:
        await target.add_roles(role, reason="Kaleb dog form")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Discord rejected the Filthy Dog role.", delete_after=8)
        return
    t_user["filthy_dog"] = {
        "by": str(ctx.author.id),
        "expires": now_ts() + 600,
        "role_id": str(role.id),
    }
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🐶 Filthy Dog",
        description=(
            f"{target.mention} is on all fours for **10 minutes**.\n"
            "They may only bark (`ARF` `BARK` `RUFF` `GRRR`) or use:\n"
            "`!bark @user` — too scared to talk for 10s\n"
            "`!chase` — nobody else here can speak for 15s\n"
            "`!bite @user` — ER timeout, 2 minutes\n"
            "`!lick` — lick the owner and drop the sub-role immediately"
        ),
        color=_c(ctx, KALEB_ROLE_KEY),
    ))


async def _ab_kaleb_copy(ctx, data, user, spec, target):
    if not _kaleb_allowed(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    target_user = get_user(data, target.id)
    source_role = target_user.get("role_key")
    if not source_role or source_role not in ROLES:
        await ctx.send("❌ That member does not hold a copyable role.", delete_after=10)
        return
    user["kaleb_copy"] = {
        "source_member_id": str(target.id),
        "role_key": source_role,
        "abilities": [entry[0] for entry in ROLES[source_role]["abilities"]],
        "expires": now_ts() + 1800,
    }
    save_data(data)
    await ctx.send(
        f"🎸 Kaleb has temporary access to **{role_display(source_role)}** abilities "
        f"for **30 minutes**. Only the copied kit is available."
    )


async def _ab_kaleb_grant_role(ctx, data, user, spec, target):
    if not _kaleb_allowed(ctx):
        await ctx.send(f"🚫 This role only works for the username `{KALEB_USERNAME}`.", delete_after=10)
        return
    parts = ctx.message.content.split(None, 2)
    role_name = parts[2].strip() if len(parts) > 2 else ""
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role is None or role.is_default() or role.managed:
        await ctx.send("❌ Provide the exact name of an existing, non-managed role.", delete_after=10)
        return
    if (
        role.permissions.administrator
        or role.permissions.ban_members
        or role.permissions.kick_members
        or role.permissions.manage_guild
        or role.permissions.manage_roles
        or role.permissions.manage_channels
        or role.permissions.manage_webhooks
    ):
        await ctx.send("❌ Kaleb cannot grant administrative or server-control roles.", delete_after=10)
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("❌ That role is above my highest role.", delete_after=10)
        return
    pre_existing = role in target.roles
    if not pre_existing:
        try:
            await target.add_roles(role, reason=f"Kaleb temporary role grant by {ctx.author}")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("❌ Discord rejected that role grant.", delete_after=10)
            return
    data.setdefault("temporary_role_grants", {})[
        f"kaleb:{ctx.guild.id}:{ctx.author.id}:{target.id}:{role.id}:{now_ts()}"
    ] = {
        "guild_id": str(ctx.guild.id),
        "member_id": str(target.id),
        "role_id": str(role.id),
        "role_name": role.name,
        "expires": now_ts() + 300,
        "pre_existing": pre_existing,
        "source": "kaleb",
        "actor_id": str(ctx.author.id),
    }
    entry = _audit_entry(
        data,
        ctx.guild,
        ctx.author,
        "kaleb_temporary_role_granted",
        target=target,
        details=f"Role **{role.name}** for 5 minutes.",
    )
    save_data(data)
    await _post_audit(ctx.guild, entry)
    await ctx.send(f"✅ {role.name} {'was already on' if pre_existing else 'was granted to'} {target.mention} for 5 minutes.")


async def _ab_green(ctx, data, user, spec, target):
    if _is_immune(get_user(data, target.id)):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    apply_effect(data, target.id, "mute", 180, ctx.author.id)
    extra = None
    pool = [m for m in ctx.guild.members if not m.bot and m.id not in (ctx.author.id, target.id) and not _is_immune(get_user(data, m.id))]
    if pool and random.random() < 0.40:
        extra = random.choice(pool[:40])
        apply_effect(data, extra.id, "mute", 60, ctx.author.id)
    save_data(data)
    line = f"{target.mention} is muted for **3 minutes**."
    if extra:
        line += f"\nGreen splashed — {extra.mention} is muted for **1 minute** too."
    await ctx.send(embed=discord.Embed(title="🟢 Green", description=line, color=discord.Color.from_rgb(90, 180, 90)))


async def _ab_purple(ctx, data, user, spec, target):
    parts = ctx.message.content.split()
    # !purple @user effect
    effect = parts[2].lower() if len(parts) > 2 else ""
    if not effect or effect not in EFFECTS:
        listing = "\n".join(f"`{k}` — {v['icon']} {v['name']}: {v['desc']}" for k, v in EFFECTS.items())
        await ctx.send(embed=discord.Embed(
            title="🟣 Purple — pick an effect",
            description=f"Usage: `!purple @user <effect>`\n\n{listing}",
            color=discord.Color.purple(),
        ))
        # refund cooldown so listing is free
        user["cooldowns"].pop("purple", None)
        save_data(data)
        return
    if _is_immune(get_user(data, target.id)):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    duration = 180
    if effect == "timeout":
        await apply_timeout(target, duration, f"!purple by {ctx.author}")
    apply_effect(data, target.id, effect, duration, ctx.author.id)
    save_data(data)
    meta = EFFECTS[effect]
    await ctx.send(embed=discord.Embed(
        title="🟣 Purple",
        description=f"{ctx.author.mention} lays {meta['icon']} **{meta['name']}** on {target.mention} for 3 minutes.\n*{meta['desc']}*",
        color=discord.Color.purple(),
    ))


async def _ab_grey_blue(ctx, data, user, spec, target):
    t_user = get_user(data, target.id)
    if _is_immune(t_user):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    t_user["grey_blue_mark"] = {"by": str(ctx.author.id), "marked_at": now_ts(), "expires": now_ts() + 300}
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🔘 Grey-Blue",
        description=(
            f"{target.mention} is marked. If the mark lasts longer than **5 seconds**, "
            "only short fragments of their text get through for 5 minutes."
        ),
        color=discord.Color.from_rgb(120, 140, 160),
    ))

    async def _fuse(gid: int, tid: int):
        await asyncio.sleep(5)
        fresh = load_data()
        tu = get_user(fresh, tid)
        mark = tu.get("grey_blue_mark") or {}
        if now_ts() >= mark.get("expires", 0) or _is_immune(tu):
            return
        apply_effect(fresh, tid, "speech_lag", 300, ctx.author.id)
        tu["partial_speech_until"] = now_ts() + 300
        save_data(fresh)

    asyncio.create_task(_fuse(ctx.guild.id, target.id))


async def _ab_record(ctx, data, user, spec, target):
    t_user = get_user(data, target.id)
    if _is_immune(t_user):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    role = await _ensure_named_role(ctx.guild, SHAME_ROLE, (160, 40, 40))
    if role is None:
        await ctx.send("I could not create the SHAME role.", delete_after=8)
        return
    try:
        await target.add_roles(role, reason="Recorded into SHAME")
    except (discord.Forbidden, discord.HTTPException):
        await ctx.send("Discord rejected the SHAME role.", delete_after=8)
        return
    t_user["shame"] = {
        "by": str(ctx.author.id),
        "expires": now_ts() + 20 * 60,
        "role_id": str(role.id),
        "word_limit": None,
        "word_limit_until": 0,
    }
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="📼 Record — SHAME",
        description=(
            f"{target.mention} is in **SHAME** for **20 minutes**.\n"
            "When they speak, others can delete the message, mock them (5-word limit; "
            "crossing it mutes them 10s), or stick an effect on them.\n"
            "Replies that are not degrading (`loser`, `dork`, `ew`, and the rest of the list) "
            "get flagged and pulled into SHAME too."
        ),
        color=discord.Color.dark_red(),
    ))


async def _ab_milk_carton(ctx, data, user, spec, target):
    t_user = get_user(data, target.id)
    if _is_immune(t_user):
        await ctx.send(f"{target.mention} is protected by immunity.", delete_after=8)
        return
    t_user["stinky_until"] = now_ts() + 300
    save_data(data)
    await ctx.send(embed=discord.Embed(
        title="🥛 Milk Carton",
        description=(
            f"{target.mention} is **stinky** for 5 minutes. "
            "Anyone who replies to them directly passes out for **1 minute**."
        ),
        color=discord.Color.from_rgb(200, 200, 160),
    ))


CUSTOM_ABILITIES.update({
    "rapid_farts": _ab_rapid_farts,
    "fart_beatbox": _ab_fart_beatbox,
    "fart_nuke": _ab_fart_nuke,
    "kaleb_obsess": _ab_kaleb_obsess,
    "kaleb_pawn": _ab_kaleb_pawn,
    "kaleb_copy": _ab_kaleb_copy,
    "kaleb_grant_role": _ab_kaleb_grant_role,
    "kaleb_kiss": _ab_kaleb_kiss,
    "kaleb_dog": _ab_kaleb_dog,
    "green": _ab_green,
    "purple": _ab_purple,
    "grey_blue": _ab_grey_blue,
    "record": _ab_record,
    "milk_carton": _ab_milk_carton,
})


def _is_immune(user: dict) -> bool:
    """True while this user has active gameplay immunity."""
    imm = user.get("immunity")
    return bool(imm and now_ts() < imm.get("expires", 0))


# Register commands for every ability added above.
for _key in list(ABILITY_SPEC):
    if _key not in {c.name for c in bot.commands}:
        _make_ability_command(_key)


# ═══════════════════════════════════════════════════════════════════
# EXPANSION MESSAGE HOOKS
# ═══════════════════════════════════════════════════════════════════
# Called from on_message. Returns True if the message was consumed and
# normal processing should stop.

CASTIEL_GIF_HOSTS = ("tenor.com", "giphy.com", "gfycat.com", "imgur.com/", "redgifs.com")


def _looks_like_gif(message: discord.Message) -> bool:
    """
    Distinguish an actual GIF/link embed from somebody merely typing a word.

    A message counts as a GIF only if it carries a real attachment with a
    gif/video content type, or contains a URL that is either a direct .gif/.mp4
    file or points at a known GIF host. Plain text — even the word "castiel" —
    never qualifies.
    """
    for a in message.attachments:
        ct = (a.content_type or "").lower()
        fn = (a.filename or "").lower()
        if ct.startswith(("image/gif", "video/")) or fn.endswith((".gif", ".gifv", ".mp4", ".webm")):
            return True
    low = message.content.lower()
    if "http://" not in low and "https://" not in low:
        return False
    for token in low.split():
        if not token.startswith(("http://", "https://")):
            continue
        clean = token.split("?")[0]
        if clean.endswith((".gif", ".gifv", ".mp4", ".webm")):
            return True
        if any(host in token for host in CASTIEL_GIF_HOSTS):
            return True
    return False


def _mentions_castiel(message: discord.Message) -> bool:
    low = message.content.lower()
    if "castiel" in low or "misha collins" in low:
        return True
    for a in message.attachments:
        if "castiel" in (a.filename or "").lower():
            return True
    return False


async def _expansion_hooks(message: discord.Message, data: dict, user: dict) -> bool:
    content = message.content
    low = content.lower()
    author = message.author

    # ── Lord Verity: natural-language obtainment ──
    if await _handle_verity_request(message, data):
        return True

    # ── Compelled phrase (Kneel / Master Of Humanity) ──
    comp = user.get("compelled_phrase")
    if comp and now_ts() < comp.get("expires", 0):
        if comp["phrase"] in low:
            user.pop("compelled_phrase", None)
            save_data(data)
            try:
                await message.add_reaction("👑")
            except Exception:
                pass
        # Otherwise they simply haven't complied yet; the timer handles failure.

    # ── Stop order ──
    stop = user.get("stop_order")
    if stop:
        if now_ts() < stop.get("silent_until", 0):
            # They spoke during the silence. Backwards typing is now enforced.
            stop["broke"] = True
            save_data(data)
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await author.send(
                    "✋ You spoke during **Stop**. For the next 2 minutes your messages "
                    "must be typed backwards."
                )
            except Exception:
                pass
            return True
        if stop.get("broke") and now_ts() < stop.get("backwards_until", 0):
            stripped = "".join(ch for ch in low if ch.isalnum())
            if stripped and stripped != stripped[::-1]:
                words = low.split()
                looks_backwards = all(w == w[::-1] or len(w) < 3 for w in words[:3]) if words else False
                if not looks_backwards:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    try:
                        await author.send("✋ Backwards. You were told.")
                    except Exception:
                        pass
                    return True
        if now_ts() >= stop.get("backwards_until", 0):
            user.pop("stop_order", None)
            save_data(data)

    # ── Burger passives (only for the Burger holder) ──
    if user.get("role_key") == BURGER_ROLE_KEY:
        # Passive 1: supernatural talk gives the NEXT speaker a headache.
        if any(w in low for w in SUPERNATURAL_WORDS):
            data["burger_headache_armed"] = {
                "channel_id": message.channel.id,
                "by": str(author.id),
                "at": now_ts(),
            }
            save_data(data)

        # Passive 2 (nerf): 35% of Burger's messages get a public delete button.
        if random.random() < 0.35:
            try:
                await message.channel.send(
                    f"*(a wrapper blows past)*",
                    view=_BurgerDeleteView(message.id),
                    reference=message,
                    mention_author=False,
                    delete_after=600,
                )
            except Exception:
                pass

        # Castiel GIFs get eaten and spat into a random channel.
        if _looks_like_gif(message) and _mentions_castiel(message):
            targets = [c for c in message.guild.text_channels
                       if c.id != message.channel.id
                       and c.permissions_for(message.guild.me).send_messages]
            payload = content
            files = []
            for a in message.attachments:
                try:
                    files.append(await a.to_file())
                except Exception:
                    pass
            try:
                await message.delete()
            except Exception:
                pass
            if targets:
                dest = random.choice(targets)
                try:
                    await dest.send(
                        f"🍔 *chews* ... *spits*\n"
                        f"{author.mention} tried to post Castiel in "
                        f"#{message.channel.name}. It ended up here.\n{payload}",
                        files=files or None,
                    )
                    await message.channel.send(
                        f"🍔 Something was eaten. It came out in #{dest.name}.",
                        delete_after=20)
                except Exception:
                    pass
            return True

    # ── Burger headache lands on the next speaker ──
    armed = data.get("burger_headache_armed")
    if armed and armed["channel_id"] == message.channel.id and str(author.id) != armed["by"]:
        if now_ts() - armed["at"] <= 300 and not _is_immune(user):
            data.pop("burger_headache_armed", None)
            apply_effect(data, author.id, "speech_lag", 20 * 6, armed["by"])
            save_data(data)
            try:
                await message.channel.send(
                    f"🤕 {author.mention} gets a splitting headache. "
                    "20 seconds between messages for the next 2 minutes.",
                    delete_after=20)
            except Exception:
                pass

    # ── Kaleb pawn order / Filthy Dog speech / SHAME / stinky replies ──
    if await _roleplay_message_hooks(message, data, user):
        return True

    return False


def _degrading(content: str) -> bool:
    low = content.lower()
    return any(w in low for w in DEGRADING_WORDS)


def _dog_speech_ok(content: str) -> bool:
    raw = content.strip()
    if raw.startswith("!"):
        cmd = raw[1:].split()[0].lower() if raw[1:].split() else ""
        return cmd in DOG_COMMANDS
    tokens = [t.strip("!?.") for t in raw.lower().split() if t.strip("!?.")]
    return bool(tokens) and all(t in DOG_ALLOWED for t in tokens)


async def _clear_temp_role(member: discord.Member, role_name: str):
    role = discord.utils.get(member.guild.roles, name=role_name)
    if role and role in member.roles:
        try:
            await member.remove_roles(role, reason="Temporary roleplay role ended")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def _roleplay_message_hooks(message: discord.Message, data: dict, user: dict) -> bool:
    content = message.content or ""
    low = content.lower()

    pawn = user.get("kaleb_pawned_by") or {}
    if pawn and now_ts() < pawn.get("expires", 0) and pawn.get("order"):
        needed = pawn["order"].lower()
        if not content.startswith("!") and needed not in low:
            apply_effect(data, message.author.id, "mute", 30, int(pawn.get("by") or 0))
            save_data(data)
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.author.send(f"🎸 Pawn order missed. You were supposed to say: {pawn['order']}")
            except Exception:
                pass
            return True

    dog = user.get("filthy_dog") or {}
    if dog and now_ts() < dog.get("expires", 0):
        if not _dog_speech_ok(content):
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.author.send("🐶 Filthy Dogs only bark: ARF, BARK, RUFF, GRRR — or !bark !chase !bite !lick.")
            except Exception:
                pass
            return True

    shame = user.get("shame") or {}
    if shame and now_ts() < shame.get("expires", 0) and not content.startswith("!"):
        limit = shame.get("word_limit")
        until = shame.get("word_limit_until") or 0
        if limit and now_ts() < until and len(content.split()) > limit:
            apply_effect(data, message.author.id, "mute", 10, int(shame.get("by") or 0))
            save_data(data)
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.channel.send(f"📼 {message.author.mention} talked too much. Muted 10 seconds.", delete_after=12)
            except Exception:
                pass
            return True
        view = _ShameView(message.author.id, message.id)
        try:
            await message.channel.send(
                f"📼 **SHAME** — {message.author.display_name} spoke. Pick on them, or don't.",
                view=view,
                reference=message,
                mention_author=False,
                delete_after=120,
            )
        except Exception:
            pass

    # Replies to a shamed or stinky member.
    ref = message.reference
    if ref and ref.message_id and not content.startswith("!"):
        try:
            replied = await message.channel.fetch_message(ref.message_id)
        except Exception:
            replied = None
        if replied and not replied.author.bot:
            other = get_user(data, replied.author.id)
            their_shame = other.get("shame") or {}
            if their_shame and now_ts() < their_shame.get("expires", 0) and not _degrading(content):
                if not _is_immune(user):
                    role = await _ensure_named_role(message.guild, SHAME_ROLE, (160, 40, 40))
                    if role:
                        try:
                            await message.author.add_roles(role, reason="Failed to degrade a SHAME target")
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                    user["shame"] = {
                        "by": their_shame.get("by"),
                        "expires": now_ts() + 20 * 60,
                        "role_id": str(role.id) if role else None,
                        "word_limit": None,
                        "word_limit_until": 0,
                    }
                    save_data(data)
                    try:
                        await message.channel.send(
                            f"📼 {message.author.mention} did not degrade them. Flagged. SHAME sticks.",
                            delete_after=15,
                        )
                    except Exception:
                        pass
            if now_ts() < (other.get("stinky_until") or 0) and not _is_immune(user):
                apply_effect(data, message.author.id, "mute", 60, replied.author.id)
                apply_effect(data, message.author.id, "ability_lock", 60, replied.author.id)
                save_data(data)
                await apply_timeout(message.author, 60, "Passed out from stink")
                try:
                    await message.channel.send(f"🥛 {message.author.mention} replied to the stink and passed out for 1 minute.")
                except Exception:
                    pass
                return True
    return False


class _ShameView(discord.ui.View):
    def __init__(self, target_id: int, message_id: int):
        super().__init__(timeout=120)
        self.target_id = target_id
        self.message_id = message_id

    async def _target(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.target_id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("They're gone.", ephemeral=True)
            return None
        data = load_data()
        if _is_immune(get_user(data, member.id)):
            await interaction.response.send_message("They are protected by immunity.", ephemeral=True)
            return None
        return member

    @discord.ui.button(label="Delete message", style=discord.ButtonStyle.danger)
    async def delete_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._target(interaction):
            return
        try:
            msg = await interaction.channel.fetch_message(self.message_id)
            await msg.delete()
            await interaction.response.send_message("Deleted.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Couldn't delete it.", ephemeral=True)

    @discord.ui.button(label="Make fun (5 words)", style=discord.ButtonStyle.secondary)
    async def mock(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await self._target(interaction)
        if not member:
            return
        data = load_data()
        tu = get_user(data, member.id)
        shame = tu.setdefault("shame", {})
        shame["word_limit"] = 5
        shame["word_limit_until"] = now_ts() + 120
        save_data(data)
        await interaction.response.send_message(
            f"{member.mention} is on a **5-word** limit for 2 minutes. Cross it and they get muted 10s.",
        )

    @discord.ui.button(label="Mute 30s", style=discord.ButtonStyle.primary)
    async def stick_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await self._target(interaction)
        if not member:
            return
        data = load_data()
        apply_effect(data, member.id, "mute", 30, interaction.user.id)
        save_data(data)
        await interaction.response.send_message(f"🤐 {member.mention} muted 30s.")


@bot.command(name="kaleb_order")
async def kaleb_order(ctx, member: discord.Member, *, phrase: str):
    """While they are your pawn, the next things they say must include this phrase."""
    data = load_data()
    if not _kaleb_allowed(ctx) or get_user(data, ctx.author.id).get("role_key") != KALEB_ROLE_KEY:
        await ctx.send("Only Kaleb can order a pawn.", delete_after=8)
        save_data(data); return
    tu = get_user(data, member.id)
    if not _pawn_live(tu, ctx.author.id) or _is_immune(tu):
        await ctx.send("That person is not your active pawn.", delete_after=8)
        save_data(data); return
    tu["kaleb_pawned_by"]["order"] = phrase[:120]
    save_data(data)
    await ctx.send(f"🎸 {member.mention} must say `{phrase[:120]}` or they get muted.")


@bot.command(name="bark")
async def bark_cmd(ctx, member: discord.Member):
    data = load_data()
    user = get_user(data, ctx.author.id)
    dog = user.get("filthy_dog") or {}
    if now_ts() >= dog.get("expires", 0):
        await ctx.send("You are not a Filthy Dog.", delete_after=8)
        save_data(data); return
    if _is_immune(get_user(data, member.id)):
        await ctx.send("They are protected by immunity.", delete_after=6)
        save_data(data); return
    apply_effect(data, member.id, "mute", 10, ctx.author.id)
    save_data(data)
    await ctx.send(f"🐶 **BARK.** {member.mention} is too scared to talk for 10 seconds.")


@bot.command(name="chase")
async def chase_cmd(ctx):
    data = load_data()
    user = get_user(data, ctx.author.id)
    dog = user.get("filthy_dog") or {}
    if now_ts() >= dog.get("expires", 0):
        await ctx.send("You are not a Filthy Dog.", delete_after=8)
        save_data(data); return
    hit = 0
    for m in ctx.guild.members:
        if m.bot or m.id == ctx.author.id:
            continue
        if _is_immune(get_user(data, m.id)):
            continue
        apply_effect(data, m.id, "mute", 15, ctx.author.id)
        hit += 1
        if hit >= 25:
            break
    save_data(data)
    await ctx.send(f"🐶 **CHASE.** {hit} people can't speak for 15 seconds. Immune members were skipped.")


@bot.command(name="bite")
async def bite_cmd(ctx, member: discord.Member):
    data = load_data()
    user = get_user(data, ctx.author.id)
    dog = user.get("filthy_dog") or {}
    if now_ts() >= dog.get("expires", 0):
        await ctx.send("You are not a Filthy Dog.", delete_after=8)
        save_data(data); return
    if _is_immune(get_user(data, member.id)):
        await ctx.send("They are protected by immunity.", delete_after=6)
        save_data(data); return
    ok = await apply_timeout(member, 120, "Filthy Dog bite — ER")
    if not ok:
        apply_effect(data, member.id, "mute", 120, ctx.author.id)
    save_data(data)
    await ctx.send(f"🐶 **BITE.** {member.mention} is sent to the ER and timed out for 2 minutes.")


@bot.command(name="lick")
async def lick_cmd(ctx):
    data = load_data()
    user = get_user(data, ctx.author.id)
    dog = user.get("filthy_dog") or {}
    if now_ts() >= dog.get("expires", 0):
        await ctx.send("You are not a Filthy Dog.", delete_after=8)
        save_data(data); return
    user.pop("filthy_dog", None)
    save_data(data)
    await _clear_temp_role(ctx.author, FILTHY_DOG_ROLE)
    await ctx.send(f"🐶 {ctx.author.mention} licks their owner. The Filthy Dog role is gone.")


@tasks.loop(seconds=30)
async def mandate_and_role_expiry_check():
    """Remove expired Mandates and only the temporary roles they granted."""
    data = load_data()
    for uid, u in list(data.get("users", {}).items()):
        dog = u.get("filthy_dog") or {}
        shame = u.get("shame") or {}
        if dog and now_ts() >= dog.get("expires", 0):
            u.pop("filthy_dog", None)
            for g in bot.guilds:
                m = g.get_member(int(uid))
                if m:
                    await _clear_temp_role(m, FILTHY_DOG_ROLE)
        if shame and now_ts() >= shame.get("expires", 0):
            u.pop("shame", None)
            for g in bot.guilds:
                m = g.get_member(int(uid))
                if m:
                    await _clear_temp_role(m, SHAME_ROLE)
    save_data(data)
    data = load_data()
    for key, record in list(data.get("mandates", {}).items()):
        guild = bot.get_guild(int(record.get("guild_id", 0)))
        member = guild.get_member(int(record.get("member_id", 0))) if guild else None
        if not guild:
            continue
        mandate_role = discord.utils.get(guild.roles, name=MANDATE_ROLE_NAME)
        if (
            now_ts() >= record.get("expires", 0)
            or member is None
            or (mandate_role and mandate_role not in member.roles)
        ):
            if member:
                await _end_mandate(guild, member, "Mandate expired or access was removed")
            else:
                data.get("mandates", {}).pop(key, None)
                await _remove_mandate_grants(guild, record, data, "Mandate expired")
                save_data(data)

    data = load_data()
    for grant_id, grant in list(data.get("temporary_role_grants", {}).items()):
        if now_ts() < grant.get("expires", 0):
            continue
        guild = bot.get_guild(int(grant.get("guild_id", 0)))
        if guild:
            member = guild.get_member(int(grant.get("member_id", 0)))
            role = guild.get_role(int(grant.get("role_id", 0)))
            if member and role and not grant.get("pre_existing") and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Temporary role grant expired")
                except (discord.Forbidden, discord.HTTPException):
                    continue
            actor = guild.get_member(int(grant.get("actor_id", 0)))
            if actor:
                entry = _audit_entry(
                    data,
                    guild,
                    actor,
                    "temporary_role_expired",
                    target=member,
                    details=f"Temporary role **{grant.get('role_name', 'unknown')}** expired.",
                    reversed_action=True,
                )
                await _post_audit(guild, entry)
        data["temporary_role_grants"].pop(grant_id, None)
    save_data(data)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.guild.id != after.guild.id:
        return
    mandate_role = discord.utils.get(after.guild.roles, name=MANDATE_ROLE_NAME)
    if mandate_role and mandate_role in before.roles and mandate_role not in after.roles:
        await _end_mandate(after.guild, after, "Mandate role access was removed")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not after.guild or after.author.bot or before.content == after.content:
        return
    data = load_data()
    record = _active_mandate(data, after.author)
    if not record:
        return
    entry = _audit_entry(
        data, after.guild, after.author, "message_edited",
        details=(
            f"#{after.channel.name}; before: {before.content[:300]!r}; "
            f"after: {after.content[:300]!r}"
        ),
        mandate_id=record.get("mandate_id"),
    )
    record.setdefault("actions", []).append(entry)
    save_data(data)
    await _post_audit(after.guild, entry)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    data = load_data()
    record = _active_mandate(data, message.author)
    if not record:
        return
    entry = _audit_entry(
        data, message.guild, message.author, "message_deleted",
        details=f"#{message.channel.name}; content: {message.content[:500]!r}",
        mandate_id=record.get("mandate_id"),
    )
    record.setdefault("actions", []).append(entry)
    save_data(data)
    await _post_audit(message.guild, entry)


@bot.event
async def on_ready():
    if not effect_janitor.is_running():
        effect_janitor.start()
    if not mandate_and_role_expiry_check.is_running():
        mandate_and_role_expiry_check.start()
    print("=" * 58)
    print(f"  Zetsubo 2.0 online as {bot.user}")
    print(f"  {len(ROLES)} roles · {len(ABILITY_SPEC)} abilities")
    print(f"  Approval gate: power >= {APPROVAL_POWER_THRESHOLD}")
    print("=" * 58)


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required.")
    bot.run(token)


if __name__ == "__main__":
    main()
