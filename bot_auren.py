from pathlib import Path

code = r'''"""
AUREN FAMILY — Discord activity bot
Python 3.11+ | discord.py 2.x

Required Railway variables:
    DISCORD_TOKEN=...
Optional:
    GUILD_ID=123456789012345678   # instant slash-command sync on one server
    DATA_DIR=/data                # mount a Railway Volume here
    TZ=Europe/Warsaw
    LEADER_ROLES=Leader,Lider,Zarząd
    CHAT_CHANNEL=💬┃chat-rodzinny
    CONTRACT_CHANNEL=🎯┃kontrakty-aktywność
    STATS_CHANNEL=✅┃statystyki
    LOTTERY_CHANNEL=🎰┃loteria
    GATHERING_CHANNEL=🗣┃zbiórka
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =============================================================================
# CONFIG
# =============================================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
TIMEZONE = ZoneInfo(os.getenv("TZ", "Europe/Warsaw"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA_DIR = Path(".")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "auren_bot.db"

CHAT_CHANNEL = os.getenv("CHAT_CHANNEL", "💬┃chat-rodzinny")
CONTRACT_CHANNEL = os.getenv("CONTRACT_CHANNEL", "🎯┃kontrakty-aktywność")
STATS_CHANNEL = os.getenv("STATS_CHANNEL", "✅┃statystyki")
LOTTERY_CHANNEL = os.getenv("LOTTERY_CHANNEL", "🎰┃loteria")
GATHERING_CHANNEL = os.getenv("GATHERING_CHANNEL", "🗣┃zbiórka")

LEADER_ROLES = {
    role.strip().casefold()
    for role in os.getenv("LEADER_ROLES", "Leader,Lider,Zarząd").split(",")
    if role.strip()
}

COURIER_COOLDOWN_MINUTES = 15
LOTTERY_MIN_POINTS = 10
LOTTERY_PRIZE = "100 000$"
LOTTERY_WEEKDAY = 6  # Sunday; Monday=0
LOTTERY_HOUR = 17
CHAOS_START_HOUR = 14
CHAOS_END_HOUR = 20

CONTRACTS = {
    "cenna": {
        "title": "Cenna partia",
        "emoji": "🔫",
        "points": 5,
        "minimum": 2,
        "join_label": "Dołącz do cennej",
        "color": 0xE74C3C,
    },
    "spisek": {
        "title": "Spisek",
        "emoji": "🧠",
        "points": 3,
        "minimum": 2,
        "join_label": "Dołącz do spisku",
        "color": 0x9B59B6,
    },
    "kable": {
        "title": "Kable",
        "emoji": "📦",
        "points": 3,
        "minimum": 5,
        "join_label": "Dołącz do kabli",
        "color": 0xF39C12,
    },
    "capt": {
        "title": "CAPT",
        "emoji": "⚔️",
        "points_win": 6,
        "points_loss": 2,
        "minimum": 1,
        "join_label": "Dołącz do CAPT",
        "color": 0xC0392B,
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("auren-bot")


# =============================================================================
# DATABASE
# =============================================================================

@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_database() -> None:
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                green INTEGER NOT NULL DEFAULT 0,
                blue INTEGER NOT NULL DEFAULT 0,
                white INTEGER NOT NULL DEFAULT 0,
                cenna INTEGER NOT NULL DEFAULT 0,
                spisek INTEGER NOT NULL DEFAULT 0,
                kable INTEGER NOT NULL DEFAULT 0,
                capt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, command)
            );

            CREATE TABLE IF NOT EXISTS active_contracts (
                guild_id INTEGER NOT NULL,
                contract_type TEXT NOT NULL,
                initiator_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, contract_type)
            );

            CREATE TABLE IF NOT EXISTS contract_participants (
                guild_id INTEGER NOT NULL,
                contract_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, contract_type, user_id),
                FOREIGN KEY (guild_id, contract_type)
                    REFERENCES active_contracts(guild_id, contract_type)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS lottery_participants (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );

            CREATE TABLE IF NOT EXISTS afk (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                since TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS capt_signup (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )


def ensure_user(guild_id: int, user_id: int) -> None:
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO users(guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )


def get_user(guild_id: int, user_id: int) -> sqlite3.Row:
    ensure_user(guild_id, user_id)
    with db() as con:
        row = con.execute(
            "SELECT * FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    assert row is not None
    return row


def add_activity(
    guild_id: int,
    user_id: int,
    activity: str,
    points: int,
) -> None:
    allowed = {"green", "blue", "white", "cenna", "spisek", "kable", "capt"}
    if activity not in allowed:
        raise ValueError(f"Unknown activity: {activity}")

    ensure_user(guild_id, user_id)
    with db() as con:
        con.execute(
            f"""
            UPDATE users
            SET points = points + ?, {activity} = {activity} + 1
            WHERE guild_id=? AND user_id=?
            """,
            (points, guild_id, user_id),
        )


def get_ranking(guild_id: int) -> list[sqlite3.Row]:
    with db() as con:
        return con.execute(
            """
            SELECT * FROM users
            WHERE guild_id=? AND points > 0
            ORDER BY points DESC, user_id ASC
            """,
            (guild_id,),
        ).fetchall()


def cooldown_remaining(
    guild_id: int,
    user_id: int,
    command: str,
    minutes: int,
) -> timedelta | None:
    with db() as con:
        row = con.execute(
            """
            SELECT used_at FROM cooldowns
            WHERE guild_id=? AND user_id=? AND command=?
            """,
            (guild_id, user_id, command),
        ).fetchone()

    if row is None:
        return None

    used_at = datetime.fromisoformat(row["used_at"])
    remaining = timedelta(minutes=minutes) - (datetime.now(TIMEZONE) - used_at)
    return remaining if remaining.total_seconds() > 0 else None


def set_cooldown(guild_id: int, user_id: int, command: str) -> None:
    with db() as con:
        con.execute(
            """
            INSERT INTO cooldowns(guild_id, user_id, command, used_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, command)
            DO UPDATE SET used_at=excluded.used_at
            """,
            (guild_id, user_id, command, datetime.now(TIMEZONE).isoformat()),
        )


def get_setting(guild_id: int, key: str) -> str | None:
    with db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE guild_id=? AND key=?",
            (guild_id, key),
        ).fetchone()
    return row["value"] if row else None


def set_setting(guild_id: int, key: str, value: str) -> None:
    with db() as con:
        con.execute(
            """
            INSERT INTO settings(guild_id, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, key)
            DO UPDATE SET value=excluded.value
            """,
            (guild_id, key, value),
        )


# =============================================================================
# HELPERS
# =============================================================================

def is_manager(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or any(role.name.casefold() in LEADER_ROLES for role in member.roles)
    )


async def reply(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            content=content,
            embed=embed,
            ephemeral=ephemeral,
            view=view,
        )
    else:
        await interaction.response.send_message(
            content=content,
            embed=embed,
            ephemeral=ephemeral,
            view=view,
        )


def find_text_channel(
    guild: discord.Guild,
    name: str,
) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=name)


async def get_or_create_channel(
    guild: discord.Guild,
    name: str,
) -> discord.TextChannel:
    channel = find_text_channel(guild, name)
    if channel:
        return channel
    return await guild.create_text_channel(name)


def split_message(text: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
            continue

        if current:
            chunks.append(current.rstrip())
            current = ""

        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current:
        chunks.append(current.rstrip())

    return chunks or ["Brak danych."]


def format_remaining(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min {seconds}s"


def next_lottery_at(now: datetime | None = None) -> datetime:
    now = now or datetime.now(TIMEZONE)
    days = (LOTTERY_WEEKDAY - now.weekday()) % 7
    result = (now + timedelta(days=days)).replace(
        hour=LOTTERY_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if result <= now:
        result += timedelta(days=7)
    return result


def lottery_countdown() -> str:
    remaining = next_lottery_at() - datetime.now(TIMEZONE)
    total_minutes = max(0, int(remaining.total_seconds() // 60))
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} dni")
    parts.append(f"{hours}h")
    parts.append(f"{minutes}min")
    return " ".join(parts)


def contract_participants(guild_id: int, contract_type: str) -> list[int]:
    with db() as con:
        rows = con.execute(
            """
            SELECT user_id FROM contract_participants
            WHERE guild_id=? AND contract_type=?
            ORDER BY joined_at ASC
            """,
            (guild_id, contract_type),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def active_contract(guild_id: int, contract_type: str) -> sqlite3.Row | None:
    with db() as con:
        return con.execute(
            """
            SELECT * FROM active_contracts
            WHERE guild_id=? AND contract_type=?
            """,
            (guild_id, contract_type),
        ).fetchone()


def contract_embed(guild_id: int, contract_type: str) -> discord.Embed:
    config = CONTRACTS[contract_type]
    contract = active_contract(guild_id, contract_type)
    participants = contract_participants(guild_id, contract_type)
    mentions = ", ".join(f"<@{uid}>" for uid in participants) or "Brak"

    if contract_type == "capt":
        details = (
            f"👤 **Inicjator:** <@{contract['initiator_id']}>\n"
            f"👥 **Uczestnicy ({len(participants)}):** {mentions}\n\n"
            f"🏆 Wygrana: **{config['points_win']} pkt** dla każdej osoby\n"
            f"❌ Przegrana: **{config['points_loss']} pkt** dla każdej osoby"
        )
    else:
        minimum = int(config["minimum"])
        missing = max(0, minimum - len(participants))
        status = (
            "✅ Minimalna liczba uczestników osiągnięta."
            if missing == 0
            else f"🕐 Potrzeba jeszcze **{missing}** osób."
        )
        details = (
            f"👤 **Inicjator:** <@{contract['initiator_id']}>\n"
            f"👥 **Uczestnicy ({len(participants)}/{minimum}):** {mentions}\n"
            f"🎯 Punkty za ukończenie: **{config['points']} pkt**\n\n"
            f"{status}"
        )

    return discord.Embed(
        title=f"{config['emoji']} AUREN — {config['title']}",
        description=details,
        color=int(config["color"]),
        timestamp=datetime.now(TIMEZONE),
    )


async def update_contract_message(
    bot: commands.Bot,
    guild_id: int,
    contract_type: str,
) -> None:
    contract = active_contract(guild_id, contract_type)
    if not contract or not contract["message_id"]:
        return

    channel = bot.get_channel(int(contract["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(contract["message_id"]))
        await message.edit(
            embed=contract_embed(guild_id, contract_type),
            view=ContractView(contract_type),
        )
    except discord.NotFound:
        log.warning("Contract message no longer exists: %s/%s", guild_id, contract_type)
    except discord.HTTPException:
        log.exception("Could not update contract message")


async def refresh_stats(guild: discord.Guild) -> None:
    channel = await get_or_create_channel(guild, STATS_CHANNEL)
    rows = get_ranking(guild.id)

    if not rows:
        report = (
            "📈 **AUREN FAMILY — STATYSTYKI AKTYWNOŚCI**\n\n"
            "Brak zapisanej aktywności."
        )
    else:
        activity_labels = {
            "green": "🌿 green",
            "blue": "💙 blue",
            "white": "🤍 white",
            "cenna": "🔫 cenna",
            "spisek": "🧠 spisek",
            "kable": "📦 kable",
            "capt": "⚔️ capt",
        }
        sections = ["📈 **AUREN FAMILY — STATYSTYKI AKTYWNOŚCI**\n"]

        for index, row in enumerate(rows, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(index, "👤")
            activities = [
                f"{label}: {row[key]}"
                for key, label in activity_labels.items()
                if int(row[key]) > 0
            ]
            sections.append(
                "━━━━━━━━━━━━━━━━━━\n"
                f"{medal} **<@{row['user_id']}>**\n"
                f"{' | '.join(activities) or 'Brak aktywności'}\n"
                f"🔢 **Suma punktów: {row['points']}**"
            )

        report = "\n".join(sections)

    try:
        async for message in channel.history(limit=100):
            if message.author == guild.me:
                await message.delete()
    except discord.HTTPException:
        log.exception("Could not clean statistics channel")

    for chunk in split_message(report):
        await channel.send(chunk)

    await channel.send(
        "Administracja może pobrać raport lub zresetować statystyki przyciskami poniżej.",
        view=StatsView(),
    )


async def refresh_lottery(guild: discord.Guild) -> None:
    channel = await get_or_create_channel(guild, LOTTERY_CHANNEL)

    with db() as con:
        rows = con.execute(
            """
            SELECT user_id FROM lottery_participants
            WHERE guild_id=? ORDER BY joined_at ASC
            """,
            (guild.id,),
        ).fetchall()

    participants = [int(row["user_id"]) for row in rows]
    mentions = "\n".join(
        f"`{index}.` <@{user_id}>"
        for index, user_id in enumerate(participants, start=1)
    ) or "*Brak zapisanych osób.*"

    embed = discord.Embed(
        title="🎰 AUREN — LOTERIA TYGODNIOWA",
        description=(
            f"Kliknij przycisk, aby dołączyć.\n"
            f"🔒 Wymagane minimum: **{LOTTERY_MIN_POINTS} pkt**\n"
            f"🎁 Nagroda: **{LOTTERY_PRIZE}**\n"
            f"🕔 Losowanie: **niedziela, 17:00**\n"
            f"⏳ Pozostało: **{lottery_countdown()}**\n\n"
            f"📋 **Uczestnicy ({len(participants)}):**\n{mentions}"
        ),
        color=0xF1C40F,
        timestamp=datetime.now(TIMEZONE),
    )

    message_id = get_setting(guild.id, "lottery_message_id")
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=embed, view=LotteryView())
            return
        except (discord.NotFound, discord.HTTPException, ValueError):
            pass

    message = await channel.send(embed=embed, view=LotteryView())
    set_setting(guild.id, "lottery_message_id", str(message.id))


async def create_contract(
    interaction: discord.Interaction,
    contract_type: str,
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await reply(interaction, "❌ Ta komenda działa wyłącznie na serwerze.")
        return

    if active_contract(interaction.guild.id, contract_type):
        await reply(
            interaction,
            f"⚠️ Kontrakt **{CONTRACTS[contract_type]['title']}** już jest aktywny.",
        )
        return

    if contract_type in {"spisek", "capt"} and not is_manager(interaction.user):
        await reply(
            interaction,
            "❌ Tę aktywność może rozpocząć tylko Leader, Lider, Zarząd lub administrator.",
        )
        return

    await interaction.response.defer(ephemeral=True)

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        channel = await get_or_create_channel(interaction.guild, CONTRACT_CHANNEL)

    now = datetime.now(TIMEZONE).isoformat()
    with db() as con:
        con.execute(
            """
            INSERT INTO active_contracts(
                guild_id, contract_type, initiator_id, channel_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                contract_type,
                interaction.user.id,
                channel.id,
                now,
            ),
        )
        con.execute(
            """
            INSERT INTO contract_participants(
                guild_id, contract_type, user_id, joined_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                contract_type,
                interaction.user.id,
                now,
            ),
        )

    message = await channel.send(
        embed=contract_embed(interaction.guild.id, contract_type),
        view=ContractView(contract_type),
    )

    with db() as con:
        con.execute(
            """
            UPDATE active_contracts SET message_id=?
            WHERE guild_id=? AND contract_type=?
            """,
            (message.id, interaction.guild.id, contract_type),
        )

    await interaction.followup.send(
        f"✅ Uruchomiono: **{CONTRACTS[contract_type]['title']}**.",
        ephemeral=True,
    )


# =============================================================================
# PERSISTENT VIEWS
# =============================================================================

class ContractView(discord.ui.View):
    def __init__(self, contract_type: str):
        super().__init__(timeout=None)
        self.contract_type = contract_type
        config = CONTRACTS[contract_type]

        join = discord.ui.Button(
            label=str(config["join_label"]),
            emoji="📥",
            style=discord.ButtonStyle.primary,
            custom_id=f"auren:{contract_type}:join",
        )
        leave = discord.ui.Button(
            label="Opuść",
            emoji="📤",
            style=discord.ButtonStyle.secondary,
            custom_id=f"auren:{contract_type}:leave",
        )
        join.callback = self.join_callback
        leave.callback = self.leave_callback
        self.add_item(join)
        self.add_item(leave)

        if contract_type == "capt":
            win = discord.ui.Button(
                label="CAPT wygrany",
                emoji="🏆",
                style=discord.ButtonStyle.success,
                custom_id="auren:capt:win",
            )
            loss = discord.ui.Button(
                label="CAPT przegrany",
                emoji="❌",
                style=discord.ButtonStyle.danger,
                custom_id="auren:capt:loss",
            )
            win.callback = self.win_callback
            loss.callback = self.loss_callback
            self.add_item(win)
            self.add_item(loss)
        else:
            finish = discord.ui.Button(
                label="Zakończ kontrakt",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"auren:{contract_type}:finish",
            )
            cancel = discord.ui.Button(
                label="Anuluj",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id=f"auren:{contract_type}:cancel",
            )
            finish.callback = self.finish_callback
            cancel.callback = self.cancel_callback
            self.add_item(finish)
            self.add_item(cancel)

    async def join_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ten kontrakt nie jest już aktywny.")
            return

        try:
            with db() as con:
                con.execute(
                    """
                    INSERT INTO contract_participants(
                        guild_id, contract_type, user_id, joined_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        interaction.guild.id,
                        self.contract_type,
                        interaction.user.id,
                        datetime.now(TIMEZONE).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            await reply(interaction, "⚠️ Jesteś już zapisany.")
            return

        await reply(
            interaction,
            f"✅ Dołączono do: **{CONTRACTS[self.contract_type]['title']}**.",
        )
        await update_contract_message(interaction.client, interaction.guild.id, self.contract_type)

    async def leave_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ten kontrakt nie jest już aktywny.")
            return

        if interaction.user.id == int(contract["initiator_id"]):
            await reply(
                interaction,
                "❌ Inicjator nie może opuścić kontraktu. Może go anulować.",
            )
            return

        with db() as con:
            result = con.execute(
                """
                DELETE FROM contract_participants
                WHERE guild_id=? AND contract_type=? AND user_id=?
                """,
                (interaction.guild.id, self.contract_type, interaction.user.id),
            )

        if result.rowcount == 0:
            await reply(interaction, "⚠️ Nie jesteś zapisany.")
            return

        await reply(interaction, "✅ Wypisano z aktywności.")
        await update_contract_message(interaction.client, interaction.guild.id, self.contract_type)

    async def finish_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, result=None)

    async def win_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, result="win")

    async def loss_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, result="loss")

    async def finish(
        self,
        interaction: discord.Interaction,
        result: str | None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna.")
            return

        allowed = (
            interaction.user.id == int(contract["initiator_id"])
            or is_manager(interaction.user)
        )
        if not allowed:
            await reply(
                interaction,
                "❌ Aktywność może zakończyć tylko inicjator lub administracja.",
            )
            return

        participants = contract_participants(interaction.guild.id, self.contract_type)
        minimum = int(CONTRACTS[self.contract_type]["minimum"])
        if len(participants) < minimum:
            await reply(
                interaction,
                f"⚠️ Potrzeba minimum **{minimum}** uczestników. Obecnie: **{len(participants)}**.",
            )
            return

        if self.contract_type == "capt":
            points = int(
                CONTRACTS["capt"]["points_win"]
                if result == "win"
                else CONTRACTS["capt"]["points_loss"]
            )
            outcome = "WYGRANY" if result == "win" else "PRZEGRANY"
        else:
            points = int(CONTRACTS[self.contract_type]["points"])
            outcome = "ZAKOŃCZONY"

        for user_id in participants:
            add_activity(
                interaction.guild.id,
                user_id,
                self.contract_type,
                points,
            )

        mentions = ", ".join(f"<@{user_id}>" for user_id in participants)
        config = CONTRACTS[self.contract_type]
        embed = discord.Embed(
            title=f"{config['emoji']} {config['title']} — {outcome}",
            description=(
                f"👤 **Zakończył:** {interaction.user.mention}\n"
                f"👥 **Uczestnicy:** {mentions}\n"
                f"⭐ **Przyznano:** {points} pkt każdej osobie\n\n"
                f"💙 **AUREN FAMILY**"
            ),
            color=0x2ECC71 if result != "loss" else 0xE67E22,
            timestamp=datetime.now(TIMEZONE),
        )

        with db() as con:
            con.execute(
                """
                DELETE FROM active_contracts
                WHERE guild_id=? AND contract_type=?
                """,
                (interaction.guild.id, self.contract_type),
            )

        try:
            await interaction.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            if interaction.channel:
                await interaction.channel.send(embed=embed)

        await reply(interaction, "✅ Aktywność została rozliczona.")
        await refresh_stats(interaction.guild)

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna.")
            return

        allowed = (
            interaction.user.id == int(contract["initiator_id"])
            or is_manager(interaction.user)
        )
        if not allowed:
            await reply(interaction, "❌ Tylko inicjator lub administracja może anulować.")
            return

        with db() as con:
            con.execute(
                """
                DELETE FROM active_contracts
                WHERE guild_id=? AND contract_type=?
                """,
                (interaction.guild.id, self.contract_type),
            )

        embed = discord.Embed(
            title="🗑️ Aktywność anulowana",
            description=(
                f"**{CONTRACTS[self.contract_type]['title']}** została anulowana "
                f"przez {interaction.user.mention}."
            ),
            color=0x95A5A6,
            timestamp=datetime.now(TIMEZONE),
        )
        try:
            await interaction.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
        await reply(interaction, "✅ Aktywność anulowana.")


class LotteryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Weź udział",
        emoji="🎟️",
        style=discord.ButtonStyle.success,
        custom_id="auren:lottery:join",
    )
    async def join(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return

        row = get_user(interaction.guild.id, interaction.user.id)
        if int(row["points"]) < LOTTERY_MIN_POINTS:
            await reply(
                interaction,
                f"❌ Potrzebujesz minimum **{LOTTERY_MIN_POINTS} pkt**. "
                f"Masz obecnie **{row['points']} pkt**.",
            )
            return

        try:
            with db() as con:
                con.execute(
                    """
                    INSERT INTO lottery_participants(guild_id, user_id, joined_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        interaction.guild.id,
                        interaction.user.id,
                        datetime.now(TIMEZONE).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            await reply(interaction, "⚠️ Jesteś już zapisany do loterii.")
            return

        await reply(interaction, "✅ Zapisano Cię do loterii AUREN. Powodzenia! 🍀")
        await refresh_lottery(interaction.guild)

    @discord.ui.button(
        label="Wypisz mnie",
        emoji="🚪",
        style=discord.ButtonStyle.secondary,
        custom_id="auren:lottery:leave",
    )
    async def leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return

        with db() as con:
            result = con.execute(
                """
                DELETE FROM lottery_participants
                WHERE guild_id=? AND user_id=?
                """,
                (interaction.guild.id, interaction.user.id),
            )

        if result.rowcount == 0:
            await reply(interaction, "⚠️ Nie jesteś zapisany do loterii.")
            return

        await reply(interaction, "✅ Wypisano Cię z loterii.")
        await refresh_lottery(interaction.guild)


class StatsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Pobierz raport",
        emoji="📁",
        style=discord.ButtonStyle.primary,
        custom_id="auren:stats:download",
    )
    async def download(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not is_manager(interaction.user):
            await reply(interaction, "❌ Tylko administracja może pobrać raport.")
            return

        rows = get_ranking(interaction.guild.id)
        lines = [
            "AUREN FAMILY — RAPORT AKTYWNOŚCI",
            f"Wygenerowano: {datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M')}",
            "",
            "Miejsce;User ID;Punkty;Green;Blue;White;Cenna;Spisek;Kable;CAPT",
        ]
        for index, row in enumerate(rows, start=1):
            lines.append(
                ";".join(
                    str(value)
                    for value in (
                        index,
                        row["user_id"],
                        row["points"],
                        row["green"],
                        row["blue"],
                        row["white"],
                        row["cenna"],
                        row["spisek"],
                        row["kable"],
                        row["capt"],
                    )
                )
            )

        path = DATA_DIR / "auren_statystyki.csv"
        path.write_text("\n".join(lines), encoding="utf-8-sig")
        await interaction.response.send_message(
            "📎 Raport AUREN:",
            file=discord.File(path),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Odśwież",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="auren:stats:refresh",
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        await refresh_stats(interaction.guild)
        await interaction.followup.send("✅ Statystyki odświeżone.", ephemeral=True)


class CaptSignupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Wystaw mnie",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="auren:signup:join",
    )
    async def join(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return

        now = datetime.now(TIMEZONE).isoformat()
        with db() as con:
            con.execute(
                """
                INSERT INTO capt_signup(guild_id, user_id, joined_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET joined_at=excluded.joined_at
                """,
                (interaction.guild.id, interaction.user.id, now),
            )

        await reply(interaction, "✅ Zapisano Cię na listę CAPT.")
        await refresh_capt_signup(interaction.guild, interaction.message)

    @discord.ui.button(
        label="Wypisz mnie",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="auren:signup:leave",
    )
    async def leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return

        with db() as con:
            row = con.execute(
                """
                SELECT joined_at FROM capt_signup
                WHERE guild_id=? AND user_id=?
                """,
                (interaction.guild.id, interaction.user.id),
            ).fetchone()

            if row is None:
                await reply(interaction, "⚠️ Nie jesteś zapisany.")
                return

            joined_at = datetime.fromisoformat(row["joined_at"])
            if datetime.now(TIMEZONE) - joined_at > timedelta(minutes=15):
                await reply(
                    interaction,
                    "❌ Minęło ponad 15 minut. Nie możesz się już samodzielnie wypisać.",
                )
                return

            con.execute(
                """
                DELETE FROM capt_signup
                WHERE guild_id=? AND user_id=?
                """,
                (interaction.guild.id, interaction.user.id),
            )

        await reply(interaction, "✅ Wypisano Cię z listy.")
        await refresh_capt_signup(interaction.guild, interaction.message)

    @discord.ui.button(
        label="Wyczyść listę",
        emoji="🧹",
        style=discord.ButtonStyle.secondary,
        custom_id="auren:signup:clear",
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not is_manager(interaction.user)
        ):
            await reply(interaction, "❌ Tylko administracja może wyczyścić listę.")
            return

        with db() as con:
            con.execute(
                "DELETE FROM capt_signup WHERE guild_id=?",
                (interaction.guild.id,),
            )

        await reply(interaction, "✅ Lista CAPT została wyczyszczona.")
        await refresh_capt_signup(interaction.guild, interaction.message)


async def refresh_capt_signup(
    guild: discord.Guild,
    message: discord.Message,
) -> None:
    with db() as con:
        rows = con.execute(
            """
            SELECT user_id, joined_at FROM capt_signup
            WHERE guild_id=? ORDER BY joined_at ASC
            """,
            (guild.id,),
        ).fetchall()

    lines = []
    for index, row in enumerate(rows, start=1):
        joined_at = datetime.fromisoformat(row["joined_at"]).astimezone(TIMEZONE)
        lines.append(
            f"`{index}.` <@{row['user_id']}> — ⏰ {joined_at.strftime('%H:%M')}"
        )

    embed = discord.Embed(
        title="🎯 AUREN — wystawienie na CAPT",
        description=(
            ("\n".join(lines) if lines else "*Brak zgłoszonych osób.*")
            + "\n\n❗ Samodzielne wypisanie jest możliwe przez 15 minut."
        ),
        color=0x3498DB,
        timestamp=datetime.now(TIMEZONE),
    )
    try:
        await message.edit(embed=embed, view=CaptSignupView())
    except discord.HTTPException:
        log.exception("Could not refresh CAPT signup")


# =============================================================================
# BOT
# =============================================================================

class AurenBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
        )
        self.synced = False

    async def setup_hook(self) -> None:
        init_database()

        for contract_type in CONTRACTS:
            self.add_view(ContractView(contract_type))
        self.add_view(LotteryView())
        self.add_view(StatsView())
        self.add_view(CaptSignupView())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s", GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally")

        for loop in (
            scheduler_loop,
            stats_refresh_loop,
            contract_reminder_loop,
        ):
            if not loop.is_running():
                loop.start()

    async def on_ready(self) -> None:
        if self.user:
            await self.change_presence(
                activity=discord.Game(name="AUREN FAMILY | /pomoc")
            )
            log.info(
                "Logged in as %s (%s), guilds=%s",
                self.user,
                self.user.id,
                len(self.guilds),
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        with db() as con:
            row = con.execute(
                """
                SELECT reason, since FROM afk
                WHERE guild_id=? AND user_id=?
                """,
                (message.guild.id, message.author.id),
            ).fetchone()

            if row:
                con.execute(
                    "DELETE FROM afk WHERE guild_id=? AND user_id=?",
                    (message.guild.id, message.author.id),
                )
                try:
                    await message.channel.send(
                        f"👋 {message.author.mention}, usunięto Twój status AFK.",
                        delete_after=8,
                    )
                except discord.HTTPException:
                    pass

            mentioned = {member.id: member for member in message.mentions}
            for member in mentioned.values():
                afk_row = con.execute(
                    """
                    SELECT reason, since FROM afk
                    WHERE guild_id=? AND user_id=?
                    """,
                    (message.guild.id, member.id),
                ).fetchone()
                if afk_row:
                    since = datetime.fromisoformat(afk_row["since"]).astimezone(TIMEZONE)
                    try:
                        await message.channel.send(
                            f"💤 {member.mention} jest AFK od "
                            f"**{since.strftime('%H:%M')}**. Powód: **{afk_row['reason']}**",
                            delete_after=12,
                        )
                    except discord.HTTPException:
                        pass

        await self.process_commands(message)


bot = AurenBot()


# =============================================================================
# SLASH COMMANDS
# =============================================================================

@bot.tree.command(name="pomoc", description="Wyświetla komendy bota AUREN")
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="💙 AUREN FAMILY — KOMENDY",
        description=(
            "**Aktywność**\n"
            "`/kuriergreen` `/kurierblue` `/kurierwhite`\n"
            "`/cenna` `/spisek` `/kable` `/capt`\n\n"
            "**Rodzina**\n"
            "`/statystyki` `/loteria` `/wystawmnie`\n"
            "`/godzinachaosu` `/biuroall` `/afk`\n\n"
            "**Administracja**\n"
            "`/reset_statystyki` `/reset_loterii` `/anuluj_aktywnosc`\n\n"
            "Dane są przechowywane w bazie SQLite na trwałym dysku."
        ),
        color=0x2980B9,
    )
    await reply(interaction, embed=embed)


@bot.tree.command(name="ping", description="Sprawdza, czy bot działa")
async def ping(interaction: discord.Interaction) -> None:
    await reply(
        interaction,
        f"🏓 Pong! Opóźnienie: **{round(bot.latency * 1000)} ms**",
    )


async def courier(interaction: discord.Interaction, courier_type: str) -> None:
    if not interaction.guild:
        await reply(interaction, "❌ Komenda działa tylko na serwerze.")
        return

    remaining = cooldown_remaining(
        interaction.guild.id,
        interaction.user.id,
        f"courier_{courier_type}",
        COURIER_COOLDOWN_MINUTES,
    )
    if remaining:
        await reply(
            interaction,
            f"⏳ Tej komendy użyjesz ponownie za **{format_remaining(remaining)}**.",
        )
        return

    chaos_active = get_setting(interaction.guild.id, "chaos_active") == "1"
    points = 3 if chaos_active else 1
    set_cooldown(
        interaction.guild.id,
        interaction.user.id,
        f"courier_{courier_type}",
    )
    add_activity(
        interaction.guild.id,
        interaction.user.id,
        courier_type,
        points,
    )

    emoji = {"green": "🌿", "blue": "💙", "white": "🤍"}[courier_type]
    embed = discord.Embed(
        title=f"{emoji} Kontrakt kurierski zakończony",
        description=(
            f"👤 {interaction.user.mention}\n"
            f"⭐ Przyznano: **{points} pkt**"
            + ("\n🌀 **Premia Godziny Chaosu ×3!**" if chaos_active else "")
        ),
        color={"green": 0x2ECC71, "blue": 0x3498DB, "white": 0xECF0F1}[courier_type],
        timestamp=datetime.now(TIMEZONE),
    )
    await reply(interaction, embed=embed, ephemeral=False)
    await refresh_stats(interaction.guild)


@bot.tree.command(name="kuriergreen", description="Zakończ kuriera green")
async def kuriergreen(interaction: discord.Interaction) -> None:
    await courier(interaction, "green")


@bot.tree.command(name="kurierblue", description="Zakończ kuriera blue")
async def kurierblue(interaction: discord.Interaction) -> None:
    await courier(interaction, "blue")


@bot.tree.command(name="kurierwhite", description="Zakończ kuriera white")
async def kurierwhite(interaction: discord.Interaction) -> None:
    await courier(interaction, "white")


@bot.tree.command(name="cenna", description="Rozpocznij grupową cenną partię")
async def cenna(interaction: discord.Interaction) -> None:
    await create_contract(interaction, "cenna")


@bot.tree.command(name="spisek", description="Rozpocznij grupowy spisek")
async def spisek(interaction: discord.Interaction) -> None:
    await create_contract(interaction, "spisek")


@bot.tree.command(name="kable", description="Rozpocznij grupowy kontrakt kable")
async def kable(interaction: discord.Interaction) -> None:
    await create_contract(interaction, "kable")


@bot.tree.command(name="capt", description="Rozpocznij akcję CAPT")
async def capt(interaction: discord.Interaction) -> None:
    await create_contract(interaction, "capt")


@bot.tree.command(name="statystyki", description="Publikuje statystyki AUREN")
async def statystyki(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_stats(interaction.guild)
    await interaction.followup.send("✅ Statystyki opublikowane.", ephemeral=True)


@bot.tree.command(name="loteria", description="Tworzy lub odświeża loterię AUREN")
async def loteria(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_lottery(interaction.guild)
    await interaction.followup.send("✅ Loteria została odświeżona.", ephemeral=True)


@bot.tree.command(name="wystawmnie", description="Tworzy listę zgłoszeń na CAPT")
async def wystawmnie(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return

    with db() as con:
        con.execute(
            """
            INSERT INTO capt_signup(guild_id, user_id, joined_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET joined_at=excluded.joined_at
            """,
            (
                interaction.guild.id,
                interaction.user.id,
                datetime.now(TIMEZONE).isoformat(),
            ),
        )

    embed = discord.Embed(
        title="🎯 AUREN — wystawienie na CAPT",
        description=f"`1.` {interaction.user.mention}\n\n❗ Wypisanie jest możliwe przez 15 minut.",
        color=0x3498DB,
    )
    await interaction.response.send_message(
        embed=embed,
        view=CaptSignupView(),
    )


@bot.tree.command(name="biuroall", description="Pięciokrotnie wzywa rodzinę na zbiórkę")
async def biuroall(interaction: discord.Interaction) -> None:
    if (
        not interaction.guild
        or not isinstance(interaction.user, discord.Member)
        or not is_manager(interaction.user)
    ):
        await reply(interaction, "❌ Komenda tylko dla administracji.")
        return

    channel = find_text_channel(interaction.guild, GATHERING_CHANNEL)
    if not channel:
        await reply(
            interaction,
            f"❌ Nie znaleziono kanału **{GATHERING_CHANNEL}**.",
        )
        return

    await reply(interaction, "✅ Rozpoczynam wezwanie na zbiórkę.")
    for index in range(5):
        await channel.send(
            "@everyone 🚨 **AUREN FAMILY — ZBIÓRKA!** "
            "Rozpoczęliśmy CAPT. Wszyscy obowiązkowo do biura! 🏃"
        )
        if index < 4:
            await asyncio.sleep(5)


@bot.tree.command(name="godzinachaosu", description="Pokazuje dzisiejszą Godzinę Chaosu")
async def godzinachaosu(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return

    active = get_setting(interaction.guild.id, "chaos_active") == "1"
    chaos_at = get_setting(interaction.guild.id, "chaos_at")

    if active:
        message = "🌀 **Godzina Chaosu trwa właśnie teraz!**"
    elif chaos_at:
        chaos_time = datetime.fromisoformat(chaos_at).astimezone(TIMEZONE)
        message = (
            f"🕒 Dzisiejsza Godzina Chaosu: "
            f"**{chaos_time.strftime('%H:%M')}–"
            f"{(chaos_time + timedelta(hours=1)).strftime('%H:%M')}**"
        )
    else:
        message = "🎲 Godzina Chaosu nie została jeszcze dzisiaj wylosowana."

    await reply(interaction, message)


@bot.tree.command(name="afk", description="Ustawia status AFK")
@app_commands.describe(powod="Opcjonalny powód nieobecności")
async def afk(
    interaction: discord.Interaction,
    powod: str = "Brak podanego powodu",
) -> None:
    if not interaction.guild:
        return
    reason = powod[:200]
    with db() as con:
        con.execute(
            """
            INSERT INTO afk(guild_id, user_id, reason, since)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET reason=excluded.reason, since=excluded.since
            """,
            (
                interaction.guild.id,
                interaction.user.id,
                reason,
                datetime.now(TIMEZONE).isoformat(),
            ),
        )
    await reply(interaction, f"💤 Ustawiono AFK. Powód: **{reason}**", ephemeral=False)


@bot.tree.command(name="reset_statystyki", description="Resetuje wszystkie statystyki")
@app_commands.describe(potwierdzenie="Wpisz RESETUJ")
async def reset_statystyki(
    interaction: discord.Interaction,
    potwierdzenie: str,
) -> None:
    if (
        not interaction.guild
        or not isinstance(interaction.user, discord.Member)
        or not is_manager(interaction.user)
    ):
        await reply(interaction, "❌ Komenda tylko dla administracji.")
        return

    if potwierdzenie.strip().upper() != "RESETUJ":
        await reply(interaction, "❌ Aby potwierdzić, wpisz dokładnie: **RESETUJ**")
        return

    with db() as con:
        con.execute("DELETE FROM users WHERE guild_id=?", (interaction.guild.id,))
        con.execute("DELETE FROM cooldowns WHERE guild_id=?", (interaction.guild.id,))

    await interaction.response.defer(ephemeral=True)
    await refresh_stats(interaction.guild)
    await interaction.followup.send("✅ Statystyki zostały zresetowane.", ephemeral=True)


@bot.tree.command(name="reset_loterii", description="Czyści listę uczestników loterii")
@app_commands.describe(potwierdzenie="Wpisz RESETUJ")
async def reset_loterii(
    interaction: discord.Interaction,
    potwierdzenie: str,
) -> None:
    if (
        not interaction.guild
        or not isinstance(interaction.user, discord.Member)
        or not is_manager(interaction.user)
    ):
        await reply(interaction, "❌ Komenda tylko dla administracji.")
        return

    if potwierdzenie.strip().upper() != "RESETUJ":
        await reply(interaction, "❌ Aby potwierdzić, wpisz dokładnie: **RESETUJ**")
        return

    with db() as con:
        con.execute(
            "DELETE FROM lottery_participants WHERE guild_id=?",
            (interaction.guild.id,),
        )

    await interaction.response.defer(ephemeral=True)
    await refresh_lottery(interaction.guild)
    await interaction.followup.send("✅ Loteria została wyczyszczona.", ephemeral=True)


@bot.tree.command(name="anuluj_aktywnosc", description="Awaryjnie usuwa aktywną akcję")
@app_commands.choices(
    typ=[
        app_commands.Choice(name="Cenna", value="cenna"),
        app_commands.Choice(name="Spisek", value="spisek"),
        app_commands.Choice(name="Kable", value="kable"),
        app_commands.Choice(name="CAPT", value="capt"),
    ]
)
async def anuluj_aktywnosc(
    interaction: discord.Interaction,
    typ: app_commands.Choice[str],
) -> None:
    if (
        not interaction.guild
        or not isinstance(interaction.user, discord.Member)
        or not is_manager(interaction.user)
    ):
        await reply(interaction, "❌ Komenda tylko dla administracji.")
        return

    contract = active_contract(interaction.guild.id, typ.value)
    if not contract:
        await reply(interaction, "⚠️ Ta aktywność nie jest uruchomiona.")
        return

    channel_id = int(contract["channel_id"])
    message_id = int(contract["message_id"]) if contract["message_id"] else None

    with db() as con:
        con.execute(
            """
            DELETE FROM active_contracts
            WHERE guild_id=? AND contract_type=?
            """,
            (interaction.guild.id, typ.value),
        )

    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel) and message_id:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=discord.Embed(
                    title="🗑️ Aktywność anulowana przez administrację",
                    description=f"Anulował: {interaction.user.mention}",
                    color=0x95A5A6,
                ),
                view=None,
            )
        except discord.HTTPException:
            pass

    await reply(interaction, "✅ Aktywność została anulowana.")


# =============================================================================
# SCHEDULED TASKS
# =============================================================================

async def send_chat(guild: discord.Guild, message: str) -> None:
    channel = find_text_channel(guild, CHAT_CHANNEL)
    if channel:
        try:
            await channel.send(message)
        except discord.HTTPException:
            log.exception("Could not send scheduled message in %s", guild.name)


@tasks.loop(minutes=1)
async def scheduler_loop() -> None:
    now = datetime.now(TIMEZONE)
    minute_key = now.strftime("%Y-%m-%d %H:%M")

    for guild in bot.guilds:
        # Morning greeting
        if now.hour == 8 and now.minute == 0:
            key = f"morning:{now.date()}"
            if get_setting(guild.id, "last_morning") != key:
                set_setting(guild.id, "last_morning", key)
                weekdays = [
                    "poniedziałek",
                    "wtorek",
                    "środa",
                    "czwartek",
                    "piątek",
                    "sobota",
                    "niedziela",
                ]
                await send_chat(
                    guild,
                    f"📅 Dziś jest **{weekdays[now.weekday()]}, "
                    f"{now.strftime('%d.%m.%Y')}**\n"
                    "💙 AUREN FAMILY — udanego dnia i dużo aktywności! 💪",
                )

        # Daily top member
        if now.hour == 10 and now.minute == 0:
            key = f"top:{now.date()}"
            if get_setting(guild.id, "last_top") != key:
                set_setting(guild.id, "last_top", key)
                ranking = get_ranking(guild.id)
                if ranking:
                    top = ranking[0]
                    await send_chat(
                        guild,
                        f"🌟 **Dzień dobry, AUREN!**\n"
                        f"🏆 Lider aktywności: <@{top['user_id']}> — "
                        f"**{top['points']} pkt**. Gratulacje! 👏",
                    )

        # Cenna reminder
        if now.hour == 14 and now.minute == 0:
            key = f"cenna:{now.date()}"
            if get_setting(guild.id, "last_cenna_reminder") != key:
                set_setting(guild.id, "last_cenna_reminder", key)
                await send_chat(
                    guild,
                    "🔫 Kontrakt **`/cenna`** jest dostępny. "
                    "Zbierz minimum 2-osobową ekipę!",
                )

        # Chaos draw once per day
        chaos_date = get_setting(guild.id, "chaos_date")
        if chaos_date != str(now.date()):
            chaos_hour = random.randint(CHAOS_START_HOUR, CHAOS_END_HOUR)
            chaos_minute = random.randint(0, 59)
            chaos_at = now.replace(
                hour=chaos_hour,
                minute=chaos_minute,
                second=0,
                microsecond=0,
            )
            set_setting(guild.id, "chaos_date", str(now.date()))
            set_setting(guild.id, "chaos_at", chaos_at.isoformat())
            set_setting(guild.id, "chaos_active", "0")
            await send_chat(
                guild,
                f"🎲 **Godzina Chaosu AUREN została wylosowana!**\n"
                f"🕒 Dzisiaj: **{chaos_at.strftime('%H:%M')}–"
                f"{(chaos_at + timedelta(hours=1)).strftime('%H:%M')}**\n"
                "Kurierzy green, blue i white będą wtedy dawać **×3 punkty**.",
            )

        chaos_at_value = get_setting(guild.id, "chaos_at")
        if chaos_at_value:
            chaos_at = datetime.fromisoformat(chaos_at_value).astimezone(TIMEZONE)
            chaos_end = chaos_at + timedelta(hours=1)
            active = get_setting(guild.id, "chaos_active") == "1"

            if chaos_at <= now < chaos_end and not active:
                set_setting(guild.id, "chaos_active", "1")
                await send_chat(
                    guild,
                    "@everyone 🌀 **GODZINA CHAOSU AUREN ROZPOCZĘTA!**\n"
                    "Przez 60 minut kurierzy green, blue i white dają **×3 punkty**!",
                )
            elif now >= chaos_end and active:
                set_setting(guild.id, "chaos_active", "0")
                await send_chat(
                    guild,
                    "✅ **Godzina Chaosu zakończona.** Punktacja wraca do normy.",
                )

        # Sunday lottery reminder
        if now.weekday() == LOTTERY_WEEKDAY and now.hour == 16 and now.minute == 0:
            key = f"lottery_reminder:{now.date()}"
            if get_setting(guild.id, "last_lottery_reminder") != key:
                set_setting(guild.id, "last_lottery_reminder", key)
                lottery_channel = find_text_channel(guild, LOTTERY_CHANNEL)
                mention = lottery_channel.mention if lottery_channel else f"#{LOTTERY_CHANNEL}"
                await send_chat(
                    guild,
                    f"@everyone 🎰 **Loteria AUREN za godzinę!**\n"
                    f"Zapisz się w {mention}. Wymagane: **{LOTTERY_MIN_POINTS} pkt**.",
                )

        # Sunday lottery draw
        if (
            now.weekday() == LOTTERY_WEEKDAY
            and now.hour == LOTTERY_HOUR
            and now.minute == 0
        ):
            key = f"lottery_draw:{now.date()}"
            if get_setting(guild.id, "last_lottery_draw") != key:
                set_setting(guild.id, "last_lottery_draw", key)
                with db() as con:
                    rows = con.execute(
                        """
                        SELECT user_id FROM lottery_participants
                        WHERE guild_id=?
                        """,
                        (guild.id,),
                    ).fetchall()

                if rows:
                    winner = random.choice(rows)["user_id"]
                    result = (
                        f"@everyone 🎉 **WYNIKI LOTERII AUREN!**\n"
                        f"Nagrodę **{LOTTERY_PRIZE}** zdobywa <@{winner}>! 🤑\n"
                        "Gratulacje!"
                    )
                else:
                    result = "🎰 Loteria AUREN zakończona — brak uczestników."

                lottery_channel = find_text_channel(guild, LOTTERY_CHANNEL)
                if lottery_channel:
                    await lottery_channel.send(result)
                await send_chat(guild, result)

                with db() as con:
                    con.execute(
                        "DELETE FROM lottery_participants WHERE guild_id=?",
                        (guild.id,),
                    )
                await refresh_lottery(guild)


@scheduler_loop.before_loop
async def before_scheduler() -> None:
    await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def stats_refresh_loop() -> None:
    for guild in bot.guilds:
        if find_text_channel(guild, STATS_CHANNEL):
            try:
                await refresh_stats(guild)
            except discord.HTTPException:
                log.exception("Automatic statistics refresh failed")


@stats_refresh_loop.before_loop
async def before_stats_refresh() -> None:
    await bot.wait_until_ready()


@tasks.loop(minutes=20)
async def contract_reminder_loop() -> None:
    for guild in bot.guilds:
        contract = active_contract(guild.id, "kable")
        if not contract:
            continue

        participants = contract_participants(guild.id, "kable")
        missing = max(0, int(CONTRACTS["kable"]["minimum"]) - len(participants))
        if missing <= 0:
            continue

        channel = bot.get_channel(int(contract["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    f"@everyone 📦 **Kable AUREN nadal aktywne!** "
                    f"Potrzeba jeszcze **{missing}** osób."
                )
            except discord.HTTPException:
                log.exception("Kable reminder failed")


@contract_reminder_loop.before_loop
async def before_contract_reminder() -> None:
    await bot.wait_until_ready()


# =============================================================================
# ERROR HANDLING
# =============================================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    log.exception(
        "Slash command error: command=%s user=%s guild=%s",
        interaction.command.name if interaction.command else "unknown",
        interaction.user.id,
        interaction.guild.id if interaction.guild else None,
        exc_info=error,
    )

    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"⏳ Spróbuj ponownie za **{round(error.retry_after)} s**."
    elif isinstance(error, app_commands.MissingPermissions):
        message = "❌ Nie masz wymaganych uprawnień."
    else:
        message = (
            "❌ Wystąpił nieoczekiwany błąd. "
            "Administracja może sprawdzić szczegóły w logach Railway."
        )

    try:
        await reply(interaction, message)
    except discord.HTTPException:
        pass


# =============================================================================
# START
# =============================================================================

def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "Brak zmiennej DISCORD_TOKEN. Dodaj token w Railway → Variables."
        )
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
'''

path = Path("/mnt/data/bot_auren.py")
path.write_text(code, encoding="utf-8")

# Compile syntax only
import py_compile
py_compile.compile(str(path), doraise=True)

len(code.splitlines()), str(path)
