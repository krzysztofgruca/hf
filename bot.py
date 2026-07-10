from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ============================= CONFIG =============================
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
TZ = ZoneInfo(os.getenv("TZ", "Europe/Warsaw"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA_DIR = Path(".")
DB_PATH = DATA_DIR / "auren_bot.db"

CHAT_CHANNEL = os.getenv("CHAT_CHANNEL", "💬┃chat-rodzinny")
CONTRACT_CHANNEL = os.getenv("CONTRACT_CHANNEL", "🎯┃kontrakty-aktywność")
STATS_CHANNEL = os.getenv("STATS_CHANNEL", "✅┃statystyki")
LOTTERY_CHANNEL = os.getenv("LOTTERY_CHANNEL", "🎰┃loteria")
GATHERING_CHANNEL = os.getenv("GATHERING_CHANNEL", "🗣┃zbiórka")
LEADER_ROLES = {x.strip().casefold() for x in os.getenv("LEADER_ROLES", "Leader,Lider,Zarząd").split(",") if x.strip()}

COURIER_COOLDOWN_MINUTES = 15
LOTTERY_MIN_POINTS = 20
LOTTERY_PRIZE = "100 000$"

CONTRACTS = {
    "cenna": {"title": "Cenna partia", "emoji": "🔫", "points": 5, "minimum": 2, "color": 0xE74C3C},
    "spisek": {"title": "Spisek", "emoji": "🧠", "points": 3, "minimum": 2, "color": 0x9B59B6},
    "kable": {"title": "Kable", "emoji": "📦", "points": 3, "minimum": 5, "color": 0xF39C12},
    "capt": {"title": "CAPT", "emoji": "⚔️", "points_win": 6, "points_loss": 2, "minimum": 1, "color": 0xC0392B},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("auren-bot")

# ============================= DATABASE =============================
@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_database() -> None:
    with db() as con:
        con.executescript("""
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
        """)


def ensure_user(guild_id: int, user_id: int) -> None:
    with db() as con:
        con.execute("INSERT OR IGNORE INTO users(guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))


def get_user(guild_id: int, user_id: int) -> sqlite3.Row:
    ensure_user(guild_id, user_id)
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    assert row is not None
    return row


def add_activity(guild_id: int, user_id: int, activity: str, points: int) -> None:
    if activity not in {"green", "blue", "white", "cenna", "spisek", "kable", "capt", "auto"}:
        raise ValueError(activity)
    ensure_user(guild_id, user_id)
    with db() as con:
        con.execute(
            f"UPDATE users SET points=points+?, {activity}={activity}+1 WHERE guild_id=? AND user_id=?",
            (points, guild_id, user_id),
        )


def get_ranking(guild_id: int) -> list[sqlite3.Row]:
    with db() as con:
        return con.execute(
            "SELECT * FROM users WHERE guild_id=? AND points>0 ORDER BY points DESC, user_id ASC",
            (guild_id,),
        ).fetchall()


def cooldown_remaining(guild_id: int, user_id: int, command: str, minutes: int) -> timedelta | None:
    with db() as con:
        row = con.execute(
            "SELECT used_at FROM cooldowns WHERE guild_id=? AND user_id=? AND command=?",
            (guild_id, user_id, command),
        ).fetchone()
    if not row:
        return None
    remaining = timedelta(minutes=minutes) - (datetime.now(TZ) - datetime.fromisoformat(row["used_at"]))
    return remaining if remaining.total_seconds() > 0 else None


def set_cooldown(guild_id: int, user_id: int, command: str) -> None:
    with db() as con:
        con.execute("""
            INSERT INTO cooldowns(guild_id,user_id,command,used_at) VALUES(?,?,?,?)
            ON CONFLICT(guild_id,user_id,command) DO UPDATE SET used_at=excluded.used_at
        """, (guild_id, user_id, command, datetime.now(TZ).isoformat()))


def get_setting(guild_id: int, key: str) -> str | None:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)).fetchone()
    return row["value"] if row else None


def set_setting(guild_id: int, key: str, value: str) -> None:
    with db() as con:
        con.execute("""
            INSERT INTO settings(guild_id,key,value) VALUES(?,?,?)
            ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value
        """, (guild_id, key, value))

# ============================= HELPERS =============================
def is_manager(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(r.name.casefold() in LEADER_ROLES for r in member.roles)


async def reply(interaction: discord.Interaction, content: str | None = None, *, embed: discord.Embed | None = None, ephemeral: bool = True, view: discord.ui.View | None = None) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral, view=view)
    else:
        await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral, view=view)


def find_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=name)


async def get_or_create_channel(guild: discord.Guild, name: str) -> discord.TextChannel:
    return find_channel(guild, name) or await guild.create_text_channel(name)


def format_remaining(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}min" if hours else f"{minutes}min {seconds}s"


def split_text(text: str, limit: int = 1900) -> list[str]:
    chunks, current = [], ""
    for line in text.splitlines(True):
        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current.rstrip())
            current = line
    if current:
        chunks.append(current.rstrip())
    return chunks or ["Brak danych."]


def active_contract(guild_id: int, contract_type: str) -> sqlite3.Row | None:
    with db() as con:
        return con.execute(
            "SELECT * FROM active_contracts WHERE guild_id=? AND contract_type=?",
            (guild_id, contract_type),
        ).fetchone()


def participants(guild_id: int, contract_type: str) -> list[int]:
    with db() as con:
        rows = con.execute(
            "SELECT user_id FROM contract_participants WHERE guild_id=? AND contract_type=? ORDER BY joined_at",
            (guild_id, contract_type),
        ).fetchall()
    return [int(x["user_id"]) for x in rows]


def contract_embed(guild_id: int, contract_type: str) -> discord.Embed:
    cfg = CONTRACTS[contract_type]
    contract = active_contract(guild_id, contract_type)
    users = participants(guild_id, contract_type)
    mentions = ", ".join(f"<@{uid}>" for uid in users) or "Brak"
    if contract_type == "capt":
        desc = (
            f"👤 **Inicjator:** <@{contract['initiator_id']}>\n"
            f"👥 **Uczestnicy ({len(users)}):** {mentions}\n\n"
            f"🏆 Wygrana: **{cfg['points_win']} pkt**\n"
            f"❌ Przegrana: **{cfg['points_loss']} pkt**"
        )
    else:
        minimum = int(cfg["minimum"])
        missing = max(0, minimum - len(users))
        desc = (
            f"👤 **Inicjator:** <@{contract['initiator_id']}>\n"
            f"👥 **Uczestnicy ({len(users)}/{minimum}):** {mentions}\n"
            f"⭐ Punkty: **{cfg['points']} pkt**\n\n"
            + ("✅ Minimalna liczba osiągnięta." if missing == 0 else f"🕐 Potrzeba jeszcze **{missing}** osób.")
        )
    return discord.Embed(title=f"{cfg['emoji']} AUREN — {cfg['title']}", description=desc, color=int(cfg["color"]), timestamp=datetime.now(TZ))


async def update_contract_message(client: discord.Client, guild_id: int, contract_type: str) -> None:
    contract = active_contract(guild_id, contract_type)
    if not contract or not contract["message_id"]:
        return
    channel = client.get_channel(int(contract["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        msg = await channel.fetch_message(int(contract["message_id"]))
        await msg.edit(embed=contract_embed(guild_id, contract_type), view=ContractView(contract_type))
    except discord.HTTPException:
        log.exception("Nie udało się odświeżyć kontraktu")


async def refresh_stats(guild: discord.Guild) -> None:
    channel = await get_or_create_channel(guild, STATS_CHANNEL)
    rows = get_ranking(guild.id)
    labels = {"green":"🌿 green","blue":"💙 blue","white":"🤍 white","cenna":"🔫 cenna","spisek":"🧠 spisek","kable":"📦 kable","capt":"⚔️ capt"}
    blocks = ["📈 **AUREN FAMILY — STATYSTYKI AKTYWNOŚCI**\n"]
    for i, row in enumerate(rows, 1):
        medal = {1:"🥇",2:"🥈",3:"🥉"}.get(i,"👤")
        acts = " | ".join(f"{label}: {row[key]}" for key,label in labels.items() if row[key] > 0)
        blocks.append(f"━━━━━━━━━━━━━━━━━━\n{medal} **<@{row['user_id']}>**\n{acts}\n🔢 **Suma punktów: {row['points']}**")
    if not rows:
        blocks.append("Brak zapisanej aktywności.")
    try:
        async for msg in channel.history(limit=100):
            if msg.author == guild.me:
                await msg.delete()
    except discord.HTTPException:
        pass
    for chunk in split_text("\n".join(blocks)):
        await channel.send(chunk)
    await channel.send("Administracja może pobrać raport lub odświeżyć dane.", view=StatsView())


def next_lottery() -> datetime:
    now = datetime.now(TZ)
    days = (6 - now.weekday()) % 7
    result = (now + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
    return result + timedelta(days=7) if result <= now else result


async def refresh_lottery(guild: discord.Guild) -> None:
    channel = await get_or_create_channel(guild, LOTTERY_CHANNEL)
    with db() as con:
        rows = con.execute("SELECT user_id FROM lottery_participants WHERE guild_id=? ORDER BY joined_at", (guild.id,)).fetchall()
    users = [int(x["user_id"]) for x in rows]
    listing = "\n".join(f"`{i}.` <@{uid}>" for i,uid in enumerate(users,1)) or "*Brak zapisanych osób.*"
    delta = next_lottery() - datetime.now(TZ)
    total = max(0, int(delta.total_seconds()//60))
    days, rest = divmod(total, 1440)
    hours, minutes = divmod(rest, 60)
    embed = discord.Embed(
        title="🎰 AUREN — LOTERIA TYGODNIOWA",
        description=(
            f"🔒 Wymagane: **{LOTTERY_MIN_POINTS} pkt**\n"
            f"🎁 Nagroda: **{LOTTERY_PRIZE}**\n"
            f"🕔 Losowanie: **niedziela 17:00**\n"
            f"⏳ Pozostało: **{days} dni {hours}h {minutes}min**\n\n"
            f"📋 **Uczestnicy ({len(users)}):**\n{listing}"
        ),
        color=0xF1C40F,
    )
    message_id = get_setting(guild.id, "lottery_message_id")
    if message_id:
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(embed=embed, view=LotteryView())
            return
        except (discord.HTTPException, ValueError):
            pass
    msg = await channel.send(embed=embed, view=LotteryView())
    set_setting(guild.id, "lottery_message_id", str(msg.id))


async def create_contract(interaction: discord.Interaction, contract_type: str) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await reply(interaction, "❌ Komenda działa tylko na serwerze.")
        return
    if active_contract(interaction.guild.id, contract_type):
        await reply(interaction, f"⚠️ **{CONTRACTS[contract_type]['title']}** już jest aktywne.")
        return
    if contract_type in {"spisek","capt"} and not is_manager(interaction.user):
        await reply(interaction, "❌ Tę aktywność uruchamia tylko Leader, Lider, Zarząd lub administrator.")
        return
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else await get_or_create_channel(interaction.guild, CONTRACT_CHANNEL)
    now = datetime.now(TZ).isoformat()
    with db() as con:
        con.execute("INSERT INTO active_contracts(guild_id,contract_type,initiator_id,channel_id,created_at) VALUES(?,?,?,?,?)", (interaction.guild.id,contract_type,interaction.user.id,channel.id,now))
        con.execute("INSERT INTO contract_participants(guild_id,contract_type,user_id,joined_at) VALUES(?,?,?,?)", (interaction.guild.id,contract_type,interaction.user.id,now))
    msg = await channel.send(embed=contract_embed(interaction.guild.id, contract_type), view=ContractView(contract_type))
    with db() as con:
        con.execute("UPDATE active_contracts SET message_id=? WHERE guild_id=? AND contract_type=?", (msg.id,interaction.guild.id,contract_type))
    await interaction.followup.send(f"✅ Uruchomiono **{CONTRACTS[contract_type]['title']}**.", ephemeral=True)

# ============================= VIEWS =============================
class ContractView(discord.ui.View):
    def __init__(self, contract_type: str):
        super().__init__(timeout=None)
        self.contract_type = contract_type
        join = discord.ui.Button(label="Dołącz", emoji="📥", style=discord.ButtonStyle.primary, custom_id=f"auren:{contract_type}:join")
        leave = discord.ui.Button(label="Opuść", emoji="📤", style=discord.ButtonStyle.secondary, custom_id=f"auren:{contract_type}:leave")
        join.callback = self.join_callback
        leave.callback = self.leave_callback
        self.add_item(join); self.add_item(leave)
        if contract_type == "capt":
            win = discord.ui.Button(label="CAPT wygrany", emoji="🏆", style=discord.ButtonStyle.success, custom_id="auren:capt:win")
            loss = discord.ui.Button(label="CAPT przegrany", emoji="❌", style=discord.ButtonStyle.danger, custom_id="auren:capt:loss")
            win.callback = self.win_callback; loss.callback = self.loss_callback
            self.add_item(win); self.add_item(loss)
        else:
            finish = discord.ui.Button(label="Zakończ", emoji="✅", style=discord.ButtonStyle.success, custom_id=f"auren:{contract_type}:finish")
            cancel = discord.ui.Button(label="Anuluj", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id=f"auren:{contract_type}:cancel")
            finish.callback = self.finish_callback; cancel.callback = self.cancel_callback
            self.add_item(finish); self.add_item(cancel)

    async def join_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not active_contract(interaction.guild.id, self.contract_type):
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna."); return
        try:
            with db() as con:
                con.execute("INSERT INTO contract_participants(guild_id,contract_type,user_id,joined_at) VALUES(?,?,?,?)", (interaction.guild.id,self.contract_type,interaction.user.id,datetime.now(TZ).isoformat()))
        except sqlite3.IntegrityError:
            await reply(interaction, "⚠️ Jesteś już zapisany."); return
        await reply(interaction, "✅ Dołączono do aktywności.")
        await update_contract_message(interaction.client, interaction.guild.id, self.contract_type)

    async def leave_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna."); return
        if interaction.user.id == int(contract["initiator_id"]):
            await reply(interaction, "❌ Inicjator nie może opuścić aktywności. Może ją anulować."); return
        with db() as con:
            result = con.execute("DELETE FROM contract_participants WHERE guild_id=? AND contract_type=? AND user_id=?", (interaction.guild.id,self.contract_type,interaction.user.id))
        if result.rowcount == 0:
            await reply(interaction, "⚠️ Nie jesteś zapisany."); return
        await reply(interaction, "✅ Wypisano z aktywności.")
        await update_contract_message(interaction.client, interaction.guild.id, self.contract_type)

    async def finish_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, None)

    async def win_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, "win")

    async def loss_callback(self, interaction: discord.Interaction) -> None:
        await self.finish(interaction, "loss")

    async def finish(self, interaction: discord.Interaction, result: str | None) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna."); return
        if interaction.user.id != int(contract["initiator_id"]) and not is_manager(interaction.user):
            await reply(interaction, "❌ Zakończyć może tylko inicjator lub administracja."); return
        users = participants(interaction.guild.id, self.contract_type)
        minimum = int(CONTRACTS[self.contract_type]["minimum"])
        if len(users) < minimum:
            await reply(interaction, f"⚠️ Potrzeba minimum **{minimum}** osób. Obecnie: **{len(users)}**."); return
        if self.contract_type == "capt":
            points = int(CONTRACTS["capt"]["points_win"] if result == "win" else CONTRACTS["capt"]["points_loss"])
            outcome = "WYGRANY" if result == "win" else "PRZEGRANY"
        else:
            points = int(CONTRACTS[self.contract_type]["points"]); outcome = "ZAKOŃCZONY"
        for uid in users:
            add_activity(interaction.guild.id, uid, self.contract_type, points)
        with db() as con:
            con.execute("DELETE FROM active_contracts WHERE guild_id=? AND contract_type=?", (interaction.guild.id,self.contract_type))
        cfg = CONTRACTS[self.contract_type]
        embed = discord.Embed(
            title=f"{cfg['emoji']} {cfg['title']} — {outcome}",
            description=f"👤 **Zakończył:** {interaction.user.mention}\n👥 **Uczestnicy:** {', '.join(f'<@{x}>' for x in users)}\n⭐ **{points} pkt dla każdej osoby**\n\n💙 **AUREN FAMILY**",
            color=0x2ECC71 if result != "loss" else 0xE67E22,
        )
        try:
            await interaction.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
        await reply(interaction, "✅ Aktywność rozliczona.")
        await refresh_stats(interaction.guild)

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        contract = active_contract(interaction.guild.id, self.contract_type)
        if not contract:
            await reply(interaction, "❌ Ta aktywność nie jest już aktywna."); return
        if interaction.user.id != int(contract["initiator_id"]) and not is_manager(interaction.user):
            await reply(interaction, "❌ Anulować może tylko inicjator lub administracja."); return
        with db() as con:
            con.execute("DELETE FROM active_contracts WHERE guild_id=? AND contract_type=?", (interaction.guild.id,self.contract_type))
        try:
            await interaction.message.edit(embed=discord.Embed(title="🗑️ Aktywność anulowana", description=f"Anulował: {interaction.user.mention}", color=0x95A5A6), view=None)
        except discord.HTTPException:
            pass
        await reply(interaction, "✅ Aktywność anulowana.")


class LotteryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Weź udział", emoji="🎟️", style=discord.ButtonStyle.success, custom_id="auren:lottery:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        row = get_user(interaction.guild.id, interaction.user.id)
        if row["points"] < LOTTERY_MIN_POINTS:
            await reply(interaction, f"❌ Potrzebujesz **{LOTTERY_MIN_POINTS} pkt**. Masz **{row['points']} pkt**."); return
        try:
            with db() as con:
                con.execute("INSERT INTO lottery_participants(guild_id,user_id,joined_at) VALUES(?,?,?)", (interaction.guild.id,interaction.user.id,datetime.now(TZ).isoformat()))
        except sqlite3.IntegrityError:
            await reply(interaction, "⚠️ Jesteś już zapisany."); return
        await reply(interaction, "✅ Zapisano do loterii AUREN. Powodzenia! 🍀")
        await refresh_lottery(interaction.guild)

    @discord.ui.button(label="Wypisz mnie", emoji="🚪", style=discord.ButtonStyle.secondary, custom_id="auren:lottery:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        with db() as con:
            result = con.execute("DELETE FROM lottery_participants WHERE guild_id=? AND user_id=?", (interaction.guild.id,interaction.user.id))
        if result.rowcount == 0:
            await reply(interaction, "⚠️ Nie jesteś zapisany."); return
        await reply(interaction, "✅ Wypisano z loterii.")
        await refresh_lottery(interaction.guild)


class StatsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Pobierz raport", emoji="📁", style=discord.ButtonStyle.primary, custom_id="auren:stats:download")
    async def download(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
            await reply(interaction, "❌ Tylko administracja może pobrać raport."); return
        rows = get_ranking(interaction.guild.id)
        lines = ["Miejsce;User ID;Punkty;Green;Blue;White;Cenna;Spisek;Kable;CAPT"]
        for i,row in enumerate(rows,1):
            lines.append(";".join(map(str,[i,row['user_id'],row['points'],row['green'],row['blue'],row['white'],row['cenna'],row['spisek'],row['kable'],row['capt']])))
        path = DATA_DIR / "auren_statystyki.csv"
        path.write_text("\n".join(lines), encoding="utf-8-sig")
        await interaction.response.send_message("📎 Raport AUREN:", file=discord.File(path), ephemeral=True)

    @discord.ui.button(label="Odśwież", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="auren:stats:refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild:
            await interaction.response.defer(ephemeral=True)
            await refresh_stats(interaction.guild)
            await interaction.followup.send("✅ Statystyki odświeżone.", ephemeral=True)


class CaptSignupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Wystaw mnie", emoji="✅", style=discord.ButtonStyle.success, custom_id="auren:signup:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild: return
        with db() as con:
            con.execute("""INSERT INTO capt_signup(guild_id,user_id,joined_at) VALUES(?,?,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET joined_at=excluded.joined_at""",
                (interaction.guild.id,interaction.user.id,datetime.now(TZ).isoformat()))
        await reply(interaction, "✅ Zapisano na listę CAPT.")
        await refresh_capt_signup(interaction.guild, interaction.message)

    @discord.ui.button(label="Wypisz mnie", emoji="❌", style=discord.ButtonStyle.danger, custom_id="auren:signup:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild: return
        with db() as con:
            row = con.execute("SELECT joined_at FROM capt_signup WHERE guild_id=? AND user_id=?", (interaction.guild.id,interaction.user.id)).fetchone()
            if not row:
                await reply(interaction, "⚠️ Nie jesteś zapisany."); return
            if datetime.now(TZ) - datetime.fromisoformat(row["joined_at"]) > timedelta(minutes=15):
                await reply(interaction, "❌ Minęło ponad 15 minut. Poproś administrację o wypisanie."); return
            con.execute("DELETE FROM capt_signup WHERE guild_id=? AND user_id=?", (interaction.guild.id,interaction.user.id))
        await reply(interaction, "✅ Wypisano z listy.")
        await refresh_capt_signup(interaction.guild, interaction.message)

    @discord.ui.button(label="Wyczyść listę", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="auren:signup:clear")
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
            await reply(interaction, "❌ Tylko administracja może wyczyścić listę."); return
        with db() as con:
            con.execute("DELETE FROM capt_signup WHERE guild_id=?", (interaction.guild.id,))
        await reply(interaction, "✅ Lista wyczyszczona.")
        await refresh_capt_signup(interaction.guild, interaction.message)


async def refresh_capt_signup(guild: discord.Guild, message: discord.Message) -> None:
    with db() as con:
        rows = con.execute("SELECT user_id,joined_at FROM capt_signup WHERE guild_id=? ORDER BY joined_at", (guild.id,)).fetchall()
    listing = "\n".join(f"`{i}.` <@{row['user_id']}> — ⏰ {datetime.fromisoformat(row['joined_at']).astimezone(TZ).strftime('%H:%M')}" for i,row in enumerate(rows,1)) or "*Brak zgłoszonych.*"
    embed = discord.Embed(title="🎯 AUREN — wystawienie na CAPT", description=listing+"\n\n❗ Samodzielne wypisanie jest możliwe przez 15 minut.", color=0x3498DB)
    await message.edit(embed=embed, view=CaptSignupView())

# ============================= BOT =============================
class AurenBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        init_database()
        for contract_type in CONTRACTS:
            self.add_view(ContractView(contract_type))
        self.add_view(LotteryView()); self.add_view(StatsView()); self.add_view(CaptSignupView())
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Komendy zsynchronizowane z serwerem %s", GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Komendy zsynchronizowane globalnie")
        for loop in (scheduler_loop, stats_refresh_loop, contract_reminder_loop):
            if not loop.is_running():
                loop.start()

    async def on_ready(self) -> None:
        if self.user:
            await self.change_presence(activity=discord.Game(name="AUREN FAMILY | /pomoc"))
            log.info("Zalogowano jako %s (%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        with db() as con:
            own = con.execute("SELECT reason FROM afk WHERE guild_id=? AND user_id=?", (message.guild.id,message.author.id)).fetchone()
            if own:
                con.execute("DELETE FROM afk WHERE guild_id=? AND user_id=?", (message.guild.id,message.author.id))
                await message.channel.send(f"👋 {message.author.mention}, usunięto status AFK.", delete_after=8)
            for member in message.mentions:
                row = con.execute("SELECT reason,since FROM afk WHERE guild_id=? AND user_id=?", (message.guild.id,member.id)).fetchone()
                if row:
                    since = datetime.fromisoformat(row["since"]).astimezone(TZ).strftime("%H:%M")
                    await message.channel.send(f"💤 {member.mention} jest AFK od **{since}**. Powód: **{row['reason']}**", delete_after=12)
        await self.process_commands(message)

bot = AurenBot()

# ============================= COMMANDS =============================
@bot.tree.command(name="pomoc", description="Pokazuje komendy AUREN")
async def pomoc(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="💙 AUREN FAMILY — KOMENDY", description=(
        "**Aktywność:** `/kuriergreen` `/kurierblue` `/kurierwhite` `/cenna` `/spisek` `/kable` `/capt`\n"
        "**Rodzina:** `/statystyki` `/loteria` `/wystawmnie` `/godzinachaosu` `/biuroall` `/afk`\n"
        "**Administracja:** `/reset_statystyki` `/reset_loterii` `/anuluj_aktywnosc`"
    ), color=0x2980B9)
    await reply(interaction, embed=embed)


@bot.tree.command(name="ping", description="Sprawdza działanie bota")
async def ping(interaction: discord.Interaction) -> None:
    await reply(interaction, f"🏓 Pong! **{round(bot.latency*1000)} ms**")


async def courier(interaction: discord.Interaction, kind: str) -> None:
    if not interaction.guild:
        await reply(interaction, "❌ Komenda działa tylko na serwerze."); return
    remaining = cooldown_remaining(interaction.guild.id, interaction.user.id, f"courier_{kind}", COURIER_COOLDOWN_MINUTES)
    if remaining:
        await reply(interaction, f"⏳ Spróbuj ponownie za **{format_remaining(remaining)}**."); return
    chaos = get_setting(interaction.guild.id, "chaos_active") == "1"
    points = 3 if chaos else 1
    set_cooldown(interaction.guild.id, interaction.user.id, f"courier_{kind}")
    add_activity(interaction.guild.id, interaction.user.id, kind, points)
    emoji = {"green":"🌿","blue":"💙","white":"🤍"}[kind]
    embed = discord.Embed(title=f"{emoji} Kontrakt kurierski zakończony", description=f"👤 {interaction.user.mention}\n⭐ **{points} pkt**" + ("\n🌀 Premia Chaosu ×3!" if chaos else ""), color={"green":0x2ECC71,"blue":0x3498DB,"white":0xECF0F1}[kind])
    await reply(interaction, embed=embed, ephemeral=False)
    await refresh_stats(interaction.guild)


@bot.tree.command(name="kuriergreen", description="Zakończ kuriera green")
async def kuriergreen(interaction: discord.Interaction) -> None: await courier(interaction, "green")
@bot.tree.command(name="kurierblue", description="Zakończ kuriera blue")
async def kurierblue(interaction: discord.Interaction) -> None: await courier(interaction, "blue")
@bot.tree.command(name="kurierwhite", description="Zakończ kuriera white")
async def kurierwhite(interaction: discord.Interaction) -> None: await courier(interaction, "white")
@bot.tree.command(name="cenna", description="Rozpocznij cenną partię")
async def cenna(interaction: discord.Interaction) -> None: await create_contract(interaction, "cenna")
@bot.tree.command(name="spisek", description="Rozpocznij spisek")
async def spisek(interaction: discord.Interaction) -> None: await create_contract(interaction, "spisek")
@bot.tree.command(name="kable", description="Rozpocznij kable")
async def kable(interaction: discord.Interaction) -> None: await create_contract(interaction, "kable")
@bot.tree.command(name="capt", description="Rozpocznij CAPT")
async def capt(interaction: discord.Interaction) -> None: await create_contract(interaction, "capt")


@bot.tree.command(name="statystyki", description="Publikuje statystyki AUREN")
async def statystyki(interaction: discord.Interaction) -> None:
    if interaction.guild:
        await interaction.response.defer(ephemeral=True)
        await refresh_stats(interaction.guild)
        await interaction.followup.send("✅ Statystyki opublikowane.", ephemeral=True)


@bot.tree.command(name="loteria", description="Tworzy lub odświeża loterię")
async def loteria(interaction: discord.Interaction) -> None:
    if interaction.guild:
        await interaction.response.defer(ephemeral=True)
        await refresh_lottery(interaction.guild)
        await interaction.followup.send("✅ Loteria odświeżona.", ephemeral=True)


@bot.tree.command(name="wystawmnie", description="Tworzy listę zgłoszeń na CAPT")
async def wystawmnie(interaction: discord.Interaction) -> None:
    if not interaction.guild: return
    with db() as con:
        con.execute("""INSERT INTO capt_signup(guild_id,user_id,joined_at) VALUES(?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET joined_at=excluded.joined_at""",
            (interaction.guild.id,interaction.user.id,datetime.now(TZ).isoformat()))
    embed = discord.Embed(title="🎯 AUREN — wystawienie na CAPT", description=f"`1.` {interaction.user.mention}\n\n❗ Wypisanie możliwe przez 15 minut.", color=0x3498DB)
    await interaction.response.send_message(embed=embed, view=CaptSignupView())


@bot.tree.command(name="biuroall", description="Wzywa rodzinę na zbiórkę")
async def biuroall(interaction: discord.Interaction) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
        await reply(interaction, "❌ Komenda tylko dla administracji."); return
    channel = find_channel(interaction.guild, GATHERING_CHANNEL)
    if not channel:
        await reply(interaction, f"❌ Nie znaleziono kanału **{GATHERING_CHANNEL}**."); return
    await reply(interaction, "✅ Rozpoczynam wezwanie.")
    for i in range(5):
        await channel.send("@everyone 🚨 **AUREN FAMILY — ZBIÓRKA!** Rozpoczęliśmy CAPT. Wszyscy do biura! 🏃")
        if i < 4: await asyncio.sleep(5)


@bot.tree.command(name="godzinachaosu", description="Pokazuje dzisiejszą Godzinę Chaosu")
async def godzinachaosu(interaction: discord.Interaction) -> None:
    if not interaction.guild: return
    if get_setting(interaction.guild.id, "chaos_active") == "1":
        text = "🌀 **Godzina Chaosu trwa teraz!**"
    elif get_setting(interaction.guild.id, "chaos_at"):
        dt = datetime.fromisoformat(get_setting(interaction.guild.id, "chaos_at")).astimezone(TZ)
        text = f"🕒 Dzisiaj: **{dt.strftime('%H:%M')}–{(dt+timedelta(hours=1)).strftime('%H:%M')}**"
    else:
        text = "🎲 Godzina Chaosu nie została jeszcze wylosowana."
    await reply(interaction, text)


@bot.tree.command(name="afk", description="Ustawia status AFK")
@app_commands.describe(powod="Opcjonalny powód")
async def afk(interaction: discord.Interaction, powod: str = "Brak podanego powodu") -> None:
    if not interaction.guild: return
    reason = powod[:200]
    with db() as con:
        con.execute("""INSERT INTO afk(guild_id,user_id,reason,since) VALUES(?,?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET reason=excluded.reason,since=excluded.since""",
            (interaction.guild.id,interaction.user.id,reason,datetime.now(TZ).isoformat()))
    await reply(interaction, f"💤 Ustawiono AFK: **{reason}**", ephemeral=False)


@bot.tree.command(name="reset_statystyki", description="Resetuje statystyki")
@app_commands.describe(potwierdzenie="Wpisz RESETUJ")
async def reset_statystyki(interaction: discord.Interaction, potwierdzenie: str) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
        await reply(interaction, "❌ Komenda tylko dla administracji."); return
    if potwierdzenie.strip().upper() != "RESETUJ":
        await reply(interaction, "❌ Wpisz dokładnie **RESETUJ**."); return
    with db() as con:
        con.execute("DELETE FROM users WHERE guild_id=?", (interaction.guild.id,))
        con.execute("DELETE FROM cooldowns WHERE guild_id=?", (interaction.guild.id,))
    await interaction.response.defer(ephemeral=True)
    await refresh_stats(interaction.guild)
    await interaction.followup.send("✅ Statystyki zresetowane.", ephemeral=True)


@bot.tree.command(name="reset_loterii", description="Czyści loterię")
@app_commands.describe(potwierdzenie="Wpisz RESETUJ")
async def reset_loterii(interaction: discord.Interaction, potwierdzenie: str) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
        await reply(interaction, "❌ Komenda tylko dla administracji."); return
    if potwierdzenie.strip().upper() != "RESETUJ":
        await reply(interaction, "❌ Wpisz dokładnie **RESETUJ**."); return
    with db() as con:
        con.execute("DELETE FROM lottery_participants WHERE guild_id=?", (interaction.guild.id,))
    await interaction.response.defer(ephemeral=True)
    await refresh_lottery(interaction.guild)
    await interaction.followup.send("✅ Loteria wyczyszczona.", ephemeral=True)


@bot.tree.command(name="anuluj_aktywnosc", description="Awaryjnie usuwa aktywną akcję")
@app_commands.choices(typ=[
    app_commands.Choice(name="Cenna", value="cenna"), app_commands.Choice(name="Spisek", value="spisek"),
    app_commands.Choice(name="Kable", value="kable"), app_commands.Choice(name="CAPT", value="capt")])
async def anuluj_aktywnosc(interaction: discord.Interaction, typ: app_commands.Choice[str]) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_manager(interaction.user):
        await reply(interaction, "❌ Komenda tylko dla administracji."); return
    contract = active_contract(interaction.guild.id, typ.value)
    if not contract:
        await reply(interaction, "⚠️ Ta aktywność nie jest uruchomiona."); return
    with db() as con:
        con.execute("DELETE FROM active_contracts WHERE guild_id=? AND contract_type=?", (interaction.guild.id,typ.value))
    await reply(interaction, "✅ Aktywność anulowana.")

# ============================= TASKS =============================
async def send_chat(guild: discord.Guild, text: str) -> None:
    channel = find_channel(guild, CHAT_CHANNEL)
    if channel:
        await channel.send(text)


@tasks.loop(minutes=1)
async def scheduler_loop() -> None:
    now = datetime.now(TZ)
    for guild in bot.guilds:
        if now.hour == 8 and now.minute == 0 and get_setting(guild.id,"morning") != str(now.date()):
            set_setting(guild.id,"morning",str(now.date()))
            days = ["poniedziałek","wtorek","środa","czwartek","piątek","sobota","niedziela"]
            await send_chat(guild, f"📅 Dziś jest **{days[now.weekday()]}, {now.strftime('%d.%m.%Y')}**\n💙 AUREN FAMILY — udanego dnia! 💪")
        if now.hour == 10 and now.minute == 0 and get_setting(guild.id,"top") != str(now.date()):
            set_setting(guild.id,"top",str(now.date()))
            rows = get_ranking(guild.id)
            if rows:
                await send_chat(guild, f"🌟 Lider aktywności AUREN: <@{rows[0]['user_id']}> — **{rows[0]['points']} pkt**! 👏")
        if now.hour == 14 and now.minute == 0 and get_setting(guild.id,"cenna_reminder") != str(now.date()):
            set_setting(guild.id,"cenna_reminder",str(now.date()))
            await send_chat(guild, "🔫 Kontrakt **`/cenna`** jest dostępny. Zbierz minimum 2 osoby!")
        if get_setting(guild.id,"chaos_date") != str(now.date()):
            dt = now.replace(hour=random.randint(14,20), minute=random.randint(0,59), second=0, microsecond=0)
            set_setting(guild.id,"chaos_date",str(now.date())); set_setting(guild.id,"chaos_at",dt.isoformat()); set_setting(guild.id,"chaos_active","0")
            await send_chat(guild, f"🎲 **Godzina Chaosu AUREN:** {dt.strftime('%H:%M')}–{(dt+timedelta(hours=1)).strftime('%H:%M')}! Kurierzy dają wtedy ×3.")
        chaos_value = get_setting(guild.id,"chaos_at")
        if chaos_value:
            dt = datetime.fromisoformat(chaos_value).astimezone(TZ)
            active = get_setting(guild.id,"chaos_active") == "1"
            if dt <= now < dt+timedelta(hours=1) and not active:
                set_setting(guild.id,"chaos_active","1")
                await send_chat(guild, "@everyone 🌀 **GODZINA CHAOSU AUREN ROZPOCZĘTA!** Kurierzy dają ×3 punkty!")
            elif now >= dt+timedelta(hours=1) and active:
                set_setting(guild.id,"chaos_active","0")
                await send_chat(guild, "✅ Godzina Chaosu zakończona.")
        if now.weekday() == 6 and now.hour == 16 and now.minute == 0 and get_setting(guild.id,"lottery_reminder") != str(now.date()):
            set_setting(guild.id,"lottery_reminder",str(now.date()))
            ch = find_channel(guild, LOTTERY_CHANNEL)
            await send_chat(guild, f"@everyone 🎰 **Loteria AUREN za godzinę!** Zapisz się w {ch.mention if ch else '#'+LOTTERY_CHANNEL}.")
        if now.weekday() == 6 and now.hour == 17 and now.minute == 0 and get_setting(guild.id,"lottery_draw") != str(now.date()):
            set_setting(guild.id,"lottery_draw",str(now.date()))
            with db() as con:
                rows = con.execute("SELECT user_id FROM lottery_participants WHERE guild_id=?", (guild.id,)).fetchall()
            result = f"@everyone 🎉 **WYNIKI LOTERII AUREN!** Nagrodę **{LOTTERY_PRIZE}** zdobywa <@{random.choice(rows)['user_id']}>! 🤑" if rows else "🎰 Loteria zakończona — brak uczestników."
            ch = find_channel(guild, LOTTERY_CHANNEL)
            if ch: await ch.send(result)
            await send_chat(guild, result)
            with db() as con:
                con.execute("DELETE FROM lottery_participants WHERE guild_id=?", (guild.id,))
            await refresh_lottery(guild)


@scheduler_loop.before_loop
async def before_scheduler() -> None: await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def stats_refresh_loop() -> None:
    for guild in bot.guilds:
        if find_channel(guild, STATS_CHANNEL):
            try: await refresh_stats(guild)
            except discord.HTTPException: log.exception("Błąd odświeżania statystyk")


@stats_refresh_loop.before_loop
async def before_stats() -> None: await bot.wait_until_ready()


@tasks.loop(minutes=20)
async def contract_reminder_loop() -> None:
    for guild in bot.guilds:
        contract = active_contract(guild.id,"kable")
        if not contract: continue
        missing = max(0, 5-len(participants(guild.id,"kable")))
        if missing:
            channel = bot.get_channel(int(contract["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                await channel.send(f"@everyone 📦 **Kable AUREN nadal aktywne!** Potrzeba jeszcze **{missing}** osób.")


@contract_reminder_loop.before_loop
async def before_contracts() -> None: await bot.wait_until_ready()

# ============================= ERRORS / START =============================
@bot.tree.error
async def tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.exception("Błąd komendy slash", exc_info=error)
    try:
        await reply(interaction, "❌ Wystąpił błąd. Szczegóły są w logach Railway.")
    except discord.HTTPException:
        pass


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Brak zmiennej DISCORD_TOKEN w Railway → Variables")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

