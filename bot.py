import discord
from discord.ext import commands
from discord import app_commands
import json
import pytz
londyn = pytz.timezone("Europe/London")
from datetime import datetime, timedelta, time  # dodaj też `time`
import asyncio
import random

# 🌀 GODZINA CHAOSU
godzina_chaosu = None  # zaplanowana godzina
aktywny_chaos = False  # czy aktualnie trwa

cooldowns_kurier = {}  # {user_id: datetime}
from discord.ui import View, Button

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Wczytywanie danych
try:
    with open("dane.json", "r") as f:
        user_data = json.load(f)
except FileNotFoundError:
    user_data = {}

try:
    with open("cooldowns.json", "r") as f:
        cooldowns = json.load(f)
except FileNotFoundError:
    cooldowns = {}

def init_user(uid):
    default = {
        "punkty": 0,
        "cenna": 0,
        "green": 0,
        "blue": 0,
        "white": 0,
        "spisek": 0,
        "kable": 0,
        "capt": 0
    }
    if uid not in user_data:
        user_data[uid] = default.copy()
    elif isinstance(user_data[uid], dict):
        for key in default:
            if key not in user_data[uid]:
                user_data[uid][key] = default[key]

def can_use_command(uid, command):
    now = datetime.utcnow()
    user_cd = cooldowns.get(uid, {})
    last_used_str = user_cd.get(command)
    cooldown_hours = {"cenna": 24, "kable": 24, "spisek": 30}.get(command, 24)
    if last_used_str:
        last_used = datetime.strptime(last_used_str, "%Y-%m-%d %H:%M:%S")
        if now - last_used < timedelta(hours=cooldown_hours):
            remaining = timedelta(hours=cooldown_hours) - (now - last_used)
            return False, remaining
    return True, None

def update_cooldown(uid, command):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    if uid not in cooldowns:
        cooldowns[uid] = {}
    cooldowns[uid][command] = now
    with open("cooldowns.json", "w") as f:
        json.dump(cooldowns, f)

def kontrakt_embed(user, typ, emoji, kolor):
    teraz = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
    return discord.Embed(
        title=f"{emoji} Kontrakt Zakończony",
        description=f"Kontrakt **{typ}** zakończony przez {user.mention}.\n📅 **Data:** {teraz}",
        color=kolor
    )

lottery_participants = {}
lottery_messages = {}

def save_lottery_data():
    with open("loteria.json", "w") as f:
        json.dump({
            "participants": {str(k): list(v) for k, v in lottery_participants.items()},
            "messages": {str(k): v for k, v in lottery_messages.items()}
        }, f)

def load_lottery_data():
    global lottery_participants, lottery_messages
    try:
        with open("loteria.json", "r") as f:
            data = json.load(f)
            lottery_participants = {int(k): set(v) for k, v in data.get("participants", {}).items()}
            lottery_messages = {int(k): v for k, v in data.get("messages", {}).items()}
    except FileNotFoundError:
        pass

async def zakoncz_kontrakt(interaction, typ, punkty, klucz, emoji, kolor, cooldown=False):
    user = interaction.user
    uid = str(user.id)
    init_user(uid)

    if cooldown:
        allowed, wait_time = can_use_command(uid, klucz)
        if not allowed:
            h, rem = divmod(wait_time.total_seconds(), 3600)
            m = int(rem // 60)
            await interaction.response.send_message(f"⏳ Odczekaj **{int(h)}h {m}min**.", ephemeral=True)
            return
        update_cooldown(uid, klucz)

    user_data[uid]["punkty"] += punkty
    user_data[uid][klucz] += 1
    with open("dane.json", "w") as f:
        json.dump(user_data, f)

    embed = kontrakt_embed(user, typ, emoji, kolor)
    await interaction.response.send_message(embed=embed)
    await odswiez_statystyki(interaction.guild)

from discord.ext import commands

from datetime import datetime, timedelta

@tree.command(name="kuriergreen", description="Zakończ kontrakt green (+1 pkt)")
async def green(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    teraz = datetime.utcnow()

    ostatni = cooldowns_kurier.get(uid)
    if ostatni and teraz - ostatni < timedelta(minutes=15):
        pozostalo = timedelta(minutes=15) - (teraz - ostatni)
        minuty, sekundy = divmod(pozostalo.total_seconds(), 60)
        await interaction.response.send_message(
            f"⏳ Możesz ponownie użyć tej komendy za **{int(minuty)}m {int(sekundy)}s**.",
            ephemeral=True
        )
        return

    cooldowns_kurier[uid] = teraz
    punkty = 3 if aktywny_chaos else 1
    await zakoncz_kontrakt(interaction, "kurier_green", punkty, "green", "🌿", 0x00ff00)


@tree.command(name="kurierblue", description="Zakończ kontrakt blue (+1 pkt)")
async def blue(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    teraz = datetime.utcnow()

    ostatni = cooldowns_kurier.get(f"blue_{uid}")
    if ostatni and teraz - ostatni < timedelta(minutes=15):
        pozostalo = timedelta(minutes=15) - (teraz - ostatni)
        minuty, sekundy = divmod(pozostalo.total_seconds(), 60)
        await interaction.response.send_message(
            f"⏳ Możesz ponownie użyć tej komendy za **{int(minuty)}m {int(sekundy)}s**.",
            ephemeral=True
        )
        return

    cooldowns_kurier[f"blue_{uid}"] = teraz
    punkty = 3 if aktywny_chaos else 1
    await zakoncz_kontrakt(interaction, "kurier_blue", punkty, "blue", "💙", 0x3498db)


@tree.command(name="kurierwhite", description="Zakończ kontrakt white (+1 pkt)")
@commands.cooldown(1, 900, commands.BucketType.user)
async def white(interaction: discord.Interaction):
    punkty = 3 if aktywny_chaos else 1
    await zakoncz_kontrakt(interaction, "kurier_white", punkty, "white", "🤍", 0xffffff)

@tree.command(name="cenna", description="Rozpocznij kontrakt cenna (grupowy, min. 2 osoby)")
async def cenna(interaction: discord.Interaction):
    view = CennaKontraktView(interaction, interaction.guild.id)
    msg = await interaction.channel.send(
        content=f"🔫 **Kontrakt CENNA rozpoczęty przez {interaction.user.mention}!**\n"
                f"Kliknij przycisk, aby dołączyć do kontraktu. Potrzeba minimum 2 osób.",
        view=view
    )
    view.kontrakt_msg = msg 
    active_cenna_contracts[interaction.guild.id]["msg_id"] = msg.id
    await interaction.response.send_message("📌 Kontrakt cenna został utworzony!", ephemeral=True)

@tree.command(name="spisek", description="Rozpocznij grupowy spisek (tylko dla leaderów i zarządu)")
async def spisek(interaction: discord.Interaction):
    # Sprawdź role
    role_names = [role.name.lower() for role in interaction.user.roles]
    if "leader" not in role_names and "zarząd" not in role_names:
        await interaction.response.send_message("❌ Tylko użytkownicy z rolą `leader` lub `zarząd` mogą rozpocząć spisek.", ephemeral=True)
        return

    # Rozpocznij spisek
    view = SpisekKontraktView(interaction, interaction.guild.id)
    msg = await interaction.channel.send(
        f"🧠 **Rozpoczęto kontrakt spisek!**\nInicjator: {interaction.user.mention}\nZalecane min. 2 osoby.",
        view=view
    )
    view.kontrakt_msg = msg
    active_spisek_contracts[interaction.guild.id]["msg_id"] = msg.id
    await interaction.response.send_message("Spisek został aktywowany!", ephemeral=True)

@tree.command(name="kable", description="Rozpocznij kontrakt grupowy kable")
async def start_kable(interaction: discord.Interaction):
    view = KableKontraktView(interaction, interaction.guild.id)
    info = (
        f"👥 Zapisani (1/5): {interaction.user.mention}\n"
        f"🕐 Potrzeba jeszcze **4** osób do rozpoczęcia kontraktu."
    )
    message = await interaction.channel.send(f"📦 **Kontrakt grupowy: kable**\n{info}", view=view)
    kontrakt = active_kable_contracts[interaction.guild.id]
    kontrakt["msg_id"] = message.id
    kontrakt["message"] = message
    await interaction.response.send_message("🚀 Kontrakt grupowy **kable** został rozpoczęty!", ephemeral=True)

# --- Statystyki i UI ---
def generuj_raport(user_data):
    emoji_map = {
        "green": "🌿 green",
        "blue": "💙 blue",
        "white": "🤍 white",
        "cenna": "🔫 cenna",
        "spisek": "🧠 spisek",
        "kable": "📦 kable",
        "capt": "⚔️ capt",
    }

    ranking = sorted(user_data.items(), key=lambda x: x[1]["punkty"], reverse=True)
    lines = []
    for i, (uid, data) in enumerate(ranking):
        if data["punkty"] <= 0:
            continue

        # Dobór emoji w zależności od miejsca
        if i == 0:
            prefix = "🥇"
        elif i == 1:
            prefix = "🥈"
        elif i == 2:
            prefix = "🥉"
        else:
            prefix = "👤"

        aktywne = [f"{emoji_map[k]}: {v}" for k, v in data.items() if k in emoji_map and v > 0]
        aktywnosci_text = " | ".join(aktywne)

        linia = (
            "━\n"
            f"{prefix} **<@{uid}>**\n"
            f"{aktywnosci_text}\n"
            f"🔢 **Suma punktów:** {data['punkty']}"
        )
        lines.append(linia)

    return f"📈 **STATYSTYKI AKTYWNOŚCI**\n\n" + "\n".join(lines)

class ResetModal(discord.ui.Modal, title="Reset Statystyk"):
    kod = discord.ui.TextInput(label="Wpisz kod resetu", placeholder="kod", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if self.kod.value == "heaven2025":
            user_data.clear()
            with open("dane.json", "w") as f:
                json.dump(user_data, f)

            kanal = discord.utils.get(interaction.guild.text_channels, name="✅┃statystyki")
            if kanal:
                async for msg in kanal.history(limit=10):
                    await msg.delete()
                raport_txt = generuj_raport(user_data)
                await kanal.send(raport_txt, view=StatystykiView(raport_txt))

            await interaction.response.send_message("✅ Statystyki zostały zresetowane!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nieprawidłowy kod.", ephemeral=True)

class StatystykiView(discord.ui.View):
    def __init__(self, raport):
        super().__init__(timeout=None)
        self.raport = raport

    @discord.ui.button(label="📁 Pobierz raport", style=discord.ButtonStyle.primary)
    async def download_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        with open("statystyki_aktywnosci.txt", "w", encoding="utf-8") as f:
            f.write(self.raport)
        await interaction.response.send_message("📎 Pobierz plik:", file=discord.File("statystyki_aktywnosci.txt"), ephemeral=True)

    @discord.ui.button(label="🔄 Resetuj statystyki", style=discord.ButtonStyle.danger)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResetModal())

@tree.command(name="statystyki", description="Utwórz lub zaktualizuj statystyki aktywności")
async def statystyki(interaction: discord.Interaction):
    raport_txt = generuj_raport(user_data)

    kanal = discord.utils.get(interaction.guild.text_channels, name="✅┃statystyki")
    if kanal is None:
        kanal = await interaction.guild.create_text_channel("✅┃statystyki")

    async for msg in kanal.history(limit=10):
        await msg.delete()

    if len(raport_txt) > 6000:
        raport_txt += "\n\n[⚠️ Uwaga: końcówka raportu mogła zostać obcięta z powodu limitu Discorda]"

    for i in range(0, len(raport_txt), 1880):
        await kanal.send(raport_txt[i:i+1880])


    # Potem osobno przyciski
    await kanal.send(view=StatystykiView(raport_txt))

    await interaction.response.send_message("📊 Statystyki zostały opublikowane!", ephemeral=True)

async def odswiez_statystyki(guild):
    kanal = discord.utils.get(guild.text_channels, name="✅┃statystyki")
    if kanal:
        async for msg in kanal.history(limit=10):
            await msg.delete()

        raport_txt = generuj_raport(user_data)

        # Wysyłanie raportu partiami
        if len(raport_txt) > 6000:
            raport_txt += "\n\n[⚠️ Uwaga: końcówka raportu mogła zostać obcięta z powodu limitu Discorda]"

        for i in range(0, len(raport_txt), 1880):
            await kanal.send(raport_txt[i:i+1880])

        # Potem same przyciski
        await kanal.send(view=StatystykiView(raport_txt))

import asyncio
from discord.ui import View, Button
import discord
import json

active_kable_contracts = {}  # {guild_id: {"inicjator": user, "uczestnicy": set, "msg_id": id, "message": msg_obj}}

async def przypomnienie_kable(guild):
    kontrakt = active_kable_contracts.get(guild.id)
    if not kontrakt:
        return

    kanal = discord.utils.get(guild.text_channels, name="🎯┃kontrakty-aktywność")
    if not kanal:
        return

    for i in range(3):
        if guild.id not in active_kable_contracts:
            break
        pozostali = 5 - len(kontrakt["uczestnicy"])
        if pozostali <= 0:
            break

        await kanal.send(
            f"@everyone 📦 Kontrakt **Kable** aktywny!\n"
            f"Potrzeba jeszcze **{pozostali}** osób do zamknięcia kontraktu.\n"
            f"Kliknij powyżej przycisk **Zapisz się na kable**, jeśli już fizycznie dostarczyłeś kabel. 📥"
        )
        await asyncio.sleep(20 * 60)

class KableKontraktView(View):
    def __init__(self, interaction, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.inicjator = interaction.user
        kontrakt = {
            "inicjator": self.inicjator,
            "uczestnicy": set([self.inicjator.id]),
            "msg_id": None,
            "message": None
        }
        active_kable_contracts[guild_id] = kontrakt
        asyncio.create_task(przypomnienie_kable(interaction.guild))

    async def update_message(self, channel):
        kontrakt = active_kable_contracts[self.guild_id]
        uczestnicy = kontrakt["uczestnicy"]
        message = kontrakt.get("message")

        mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy)
        info = (
            f"👥 Zapisani ({len(uczestnicy)}/5): {mentions}\n"
            f"🕐 Potrzeba jeszcze **{max(0, 5 - len(uczestnicy))}** osób do rozpoczęcia kontraktu."
        )

        if message:
            try:
                await message.edit(content=f"📦 **Kontrakt grupowy: kable**\n{info}", view=self)
            except:
                pass

    @discord.ui.button(label="📥 Zapisz się na kable", style=discord.ButtonStyle.primary)
    async def join_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_kable_contracts[self.guild_id]

        if interaction.user.id in kontrakt["uczestnicy"]:
            await interaction.response.send_message("❗ Już jesteś zapisany do kontraktu kable.", ephemeral=True)
            return

        if len(kontrakt["uczestnicy"]) >= 5:
            await interaction.response.send_message("❌ Limit 5 uczestników został osiągnięty.", ephemeral=True)
            return

        kontrakt["uczestnicy"].add(interaction.user.id)
        await interaction.response.send_message("✅ Dołączyłeś do kontraktu kable.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="📤 Opuść kable", style=discord.ButtonStyle.secondary)
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_kable_contracts[self.guild_id]
        if interaction.user.id in kontrakt["uczestnicy"]:
            kontrakt["uczestnicy"].remove(interaction.user.id)
            await interaction.response.send_message("🚪 Opuściłeś kontrakt kable.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nie jesteś zapisany do tego kontraktu.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="✅ Zakończ kontrakt", style=discord.ButtonStyle.success)
    async def finish_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_kable_contracts.get(self.guild_id)
        if not kontrakt:
            await interaction.response.send_message("❌ Nie znaleziono aktywnego kontraktu.", ephemeral=True)
            return

        role_names = [role.name.lower() for role in interaction.user.roles]
        if interaction.user.id != kontrakt["inicjator"].id and "lider" not in role_names:
            await interaction.response.send_message("❌ Tylko inicjator lub użytkownik z rolą `Lider` może zakończyć kontrakt.", ephemeral=True)
            return

        uczestnicy = kontrakt["uczestnicy"]
        for uid in uczestnicy:
            str_uid = str(uid)
            init_user(str_uid)
            user_data[str_uid]["punkty"] += 1
            user_data[str_uid]["kable"] += 1

        with open("dane.json", "w") as f:
            json.dump(user_data, f)

        uczestnicy_mentions = ", ".join([f"<@{uid}>" for uid in uczestnicy])
        embed = discord.Embed(
            title="📦 Kontrakt Zakończony",
            description=(
                f"Kontrakt **kable** został zakończony przez inicjatora.\n\n"
                f"👤 **Inicjator:** {kontrakt['inicjator'].mention}\n"
                f"👥 **Uczestnicy:** {uczestnicy_mentions}\n\n"
                f"🎉 Gratulacje dla wszystkich uczestników!"
            ),
            color=0xf39c12
        )

        kanal = interaction.channel
        if kontrakt["msg_id"]:
            try:
                await kontrakt["message"].edit(content="", embed=embed, view=None)
            except:
                await kanal.send(embed=embed)
        else:
            await kanal.send(embed=embed)

        del active_kable_contracts[self.guild_id]
        await odswiez_statystyki(interaction.guild)

from discord.ui import View, Button

active_cenna_contracts = {}

class CennaKontraktView(View):
    def __init__(self, interaction, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.inicjator = interaction.user
        self.kontrakt_msg = None
        active_cenna_contracts[guild_id] = {
            "inicjator": self.inicjator,
            "uczestnicy": set([self.inicjator.id]),
            "msg_id": None,
        }

    async def update_message(self, channel):
        kontrakt = active_cenna_contracts[self.guild_id]
        uczestnicy = kontrakt["uczestnicy"]
        mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy)

        content = (
            f"🔫 **Kontrakt CENNA rozpoczęty przez {self.inicjator.mention}!**\n"
            f"👥 Uczestnicy ({len(uczestnicy)}): {mentions}\n"
            f"Kliknij przycisk, aby dołączyć do kontraktu. Potrzeba minimum 2 osób."
        )

        if self.kontrakt_msg:
            try:
                await self.kontrakt_msg.edit(content=content, view=self)
            except:
                pass

    @discord.ui.button(label="📥 Zapisz się na cenną", style=discord.ButtonStyle.primary)
    async def join_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_cenna_contracts[self.guild_id]
        kontrakt["uczestnicy"].add(interaction.user.id)
        await interaction.response.send_message("✅ Dołączyłeś do kontraktu cenna.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="📤 Opuść cenną", style=discord.ButtonStyle.secondary)
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_cenna_contracts[self.guild_id]
        if interaction.user.id in kontrakt["uczestnicy"]:
            kontrakt["uczestnicy"].remove(interaction.user.id)
            await interaction.response.send_message("🚪 Opuściłeś kontrakt cenna.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nie jesteś zapisany do tego kontraktu.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="✅ Zakończ kontrakt", style=discord.ButtonStyle.success)
    async def finish_button(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_cenna_contracts.get(self.guild_id)
        if not kontrakt or kontrakt["inicjator"].id != interaction.user.id:
            await interaction.response.send_message("❌ Tylko inicjator może zakończyć kontrakt.", ephemeral=True)
            return

        uczestnicy = kontrakt["uczestnicy"]
        if len(uczestnicy) < 2:
            await interaction.response.send_message("⚠️ Do zakończenia kontraktu **cenna** potrzebne są minimum 2 osoby.", ephemeral=True)
            return

        for uid in uczestnicy:
            str_uid = str(uid)
            init_user(str_uid)
            user_data[str_uid]["punkty"] += 2
            user_data[str_uid]["cenna"] += 1
        with open("dane.json", "w") as f:
            json.dump(user_data, f)

        mentions = ", ".join([f"<@{uid}>" for uid in uczestnicy])
        embed = discord.Embed(
            title="🔫 Kontrakt Zakończony",
            description=(
                f"Kontrakt **cenna partia** został zakończony przez inicjatora.\n\n"
                f"👤 **Inicjator:** {kontrakt['inicjator'].mention}\n"
                f"👥 **Uczestnicy:** {mentions}\n\n"
                f"🎉 Gratulacje dla wszystkich uczestników!"
            ),
            color=0xff0000
        )

        kanal = interaction.channel
        if kontrakt["msg_id"]:
            try:
                await self.kontrakt_msg.edit(content="", embed=embed, view=None)
            except:
                await kanal.send(embed=embed)
        else:
            await kanal.send(embed=embed)

        del active_cenna_contracts[self.guild_id]
        await odswiez_statystyki(interaction.guild)

active_spisek_contracts = {}

class SpisekKontraktView(View):
    def __init__(self, interaction, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.inicjator = interaction.user
        self.kontrakt_msg = None
        active_spisek_contracts[guild_id] = {
            "inicjator": self.inicjator,
            "uczestnicy": set([self.inicjator.id]),
            "msg_id": None,
        }

    async def update_message(self, channel):
        kontrakt = active_spisek_contracts[self.guild_id]
        uczestnicy = kontrakt["uczestnicy"]
        mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy)

        content = (
            f"🧠 **Rozpoczęto kontrakt spisek!**\n"
            f"👤 Inicjator: {self.inicjator.mention}\n"
            f"👥 Uczestnicy ({len(uczestnicy)}): {mentions}\n"
            f"Zalecane min. 2 osoby."
        )

        if self.kontrakt_msg:
            try:
                await self.kontrakt_msg.edit(content=content, view=self)
            except:
                pass

    @discord.ui.button(label="🧠 Dołącz do spisku", style=discord.ButtonStyle.primary)
    async def join_spisek(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_spisek_contracts[self.guild_id]
        kontrakt["uczestnicy"].add(interaction.user.id)
        await interaction.response.send_message("✅ Dołączyłeś do spisku.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="🚪 Opuść spisek", style=discord.ButtonStyle.secondary)
    async def leave_spisek(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_spisek_contracts[self.guild_id]
        if interaction.user.id in kontrakt["uczestnicy"]:
            kontrakt["uczestnicy"].remove(interaction.user.id)
            await interaction.response.send_message("👋 Opuściłeś spisek.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nie jesteś zapisany do spisku.", ephemeral=True)
        await self.update_message(interaction.channel)

    @discord.ui.button(label="✅ Zakończ spisek", style=discord.ButtonStyle.success)
    async def finish_spisek(self, interaction: discord.Interaction, button: Button):
        kontrakt = active_spisek_contracts.get(self.guild_id)
        if not kontrakt or kontrakt["inicjator"].id != interaction.user.id:
            await interaction.response.send_message("❌ Tylko inicjator może zakończyć spisek.", ephemeral=True)
            return

        uczestnicy = kontrakt["uczestnicy"]
        for uid in uczestnicy:
            str_uid = str(uid)
            init_user(str_uid)
            user_data[str_uid]["punkty"] += 1
            user_data[str_uid]["spisek"] += 1
        with open("dane.json", "w") as f:
            json.dump(user_data, f)

        mentions = ", ".join([f"<@{uid}>" for uid in uczestnicy])
        embed = discord.Embed(
            title="🧠 Spisek zakończony",
            description=(
                f"Kontrakt **spisek** został zakończony przez inicjatora.\n\n"
                f"👤 **Inicjator:** {kontrakt['inicjator'].mention}\n"
                f"👥 **Uczestnicy:** {mentions}\n\n"
                f"🔎 Zalecana liczba uczestników: 2+\n🎉 Punkty przyznane!"
            ),
            color=0x9b59b6
        )

        kanal = interaction.channel
        if kontrakt["msg_id"]:
            try:
                await self.kontrakt_msg.edit(content="", embed=embed, view=None)
            except:
                await kanal.send(embed=embed)
        else:
            await kanal.send(embed=embed)

        del active_spisek_contracts[self.guild_id]
        await odswiez_statystyki(interaction.guild)

        kanal = interaction.channel
        if kontrakt["msg_id"]:
            try:
                msg = await kanal.fetch_message(kontrakt["msg_id"])
                await msg.edit(content="", embed=embed, view=None)
            except:
                await kanal.send(embed=embed)
        else:
            await kanal.send(embed=embed)

        del active_spisek_contracts[self.guild_id]
        await odswiez_statystyki(interaction.guild)

from discord.ext import tasks

@tasks.loop(minutes=1)
async def przypomnienie_cenna():
    await bot.wait_until_ready()
    teraz = datetime.now()
    if teraz.hour == 13 and teraz.minute == 0:
        for guild in bot.guilds:
            kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
            if kanal:
                await kanal.send(
                    "📢 **Godzina 14:00 – można dokonywać napadów na biznes!**\n"
                    "Jeśli kontrakt `cenna` nie jest zamrożony, prosimy o jego wykonanie 💣💼"
                )

@tasks.loop(minutes=1)
async def ogloszenie_top_usera():
    teraz = datetime.now()
    if teraz.hour == 10 and teraz.minute == 0:
        for guild in bot.guilds:
            kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
            if not kanal:
                continue

            if not user_data:
                continue

            top = sorted(user_data.items(), key=lambda x: x[1]["punkty"], reverse=True)
            if not top or top[0][1]["punkty"] <= 0:
                continue

            uid, dane = top[0]
            await kanal.send(
                f"🌟 **Dzień dobry!**\n\n"
                f"🏆 Aktualnym liderem aktywności jest: <@{uid}> z **{dane['punkty']} punktami**!\n"
                f"Gratulacje za zaangażowanie! 👏\n\n"
                f"🚀 Dołącz do rywalizacji – każda aktywność się liczy!"
            )

@tasks.loop(minutes=1)
async def chaos_loop():
    global aktywny_chaos, godzina_chaosu
    teraz = datetime.now(pytz.timezone("Europe/Warsaw"))

    print(f"[CHAOS DEBUG] teraz={teraz.strftime('%H:%M')}, godzina_chaosu={godzina_chaosu}")

    if godzina_chaosu and teraz.hour == godzina_chaosu.hour and teraz.minute == godzina_chaosu.minute:
        if not aktywny_chaos:
            aktywny_chaos = True
            print("[CHAOS] Godzina Chaosu rozpoczęta!")

            for guild in bot.guilds:
                kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
                if kanal:
                    await kanal.send("@everyone ⚠️ **GODZINA CHAOSU ROZPOCZĘTA!**\n"
                                     "Wszystkie kontrakty `/kuriergreen`, `/kurierblue` i `/kurierwhite` dają **x3 punkty** przez 60 minut!")

            asyncio.create_task(zakonczenie_chaosu())

async def zakonczzenie_chaosu():
    global aktywny_chaos, godzina_chaosu
    await asyncio.sleep(60 * 60)  # 60 minut
    aktywny_chaos = False
    godzina_chaosu = None
    print("[CHAOS] Chaos zakończony.")

    for guild in bot.guilds:
        kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
        if kanal:
            await kanal.send("✅ **Godzina Chaosu zakończona!** Wszystko wraca do normy.")


@tasks.loop(minutes=1)
async def losuj_godzine_chaosu():
    global godzina_chaosu
    teraz = datetime.now(pytz.timezone("Europe/Warsaw"))

    if godzina_chaosu is None:
        losowa_godzina = random.randint(14, 20)  # <-- zmieniony zakres
        losowa_minuta = random.randint(0, 59)
        godzina_chaosu = datetime.strptime(f"{losowa_godzina}:{losowa_minuta}", "%H:%M").time()

        print(f"[CHAOS] Wylosowano: {godzina_chaosu.strftime('%H:%M')}")

        for guild in bot.guilds:
            kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
            if kanal:
                await kanal.send(
                    f"📢 **Godzina Chaosu** została wylosowana!\n"
                    f"🎲 Kontrakty `/kuriergreen`, `/kurierblue` i `/kurierwhite` będą liczone x3 "
                    f"w godzinie **{godzina_chaosu.strftime('%H:%M')} - "
                    f"{(datetime.combine(datetime.today(), godzina_chaosu) + timedelta(hours=1)).time().strftime('%H:%M')}**!"
                )
                
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Zalogowano jako {bot.user}")
    print("📤 Komendy zostały zsynchronizowane globalnie.")

    # 🌀 TESTOWE WYMUSZENIE GODZINY CHAOSU
    from datetime import datetime, timedelta
    global godzina_chaosu
    godzina_chaosu = (datetime.now() + timedelta(minutes=1)).time()
    print(f"[DEBUG] Ustawiono testową godzinę chaosu na: {godzina_chaosu}")

    # 📂 Wczytaj dane loterii z pliku
    load_lottery_data()
    print("📂 Dane loterii wczytane:", lottery_participants)

    # 🔁 Przywróć widoki do istniejących wiadomości loterii
    for guild in bot.guilds:
        if guild.id in lottery_messages:
            bot.add_view(LotteryView(guild.id))

    # 🚀 Uruchom zaplanowane zadania
    przypomnienie_loteria.start()
    uruchom_loterie.start()
    przypomnienie_cenna.start()
    ogloszenie_top_usera.start()
    chaos_loop.start()               
    losuj_godzine_chaosu.start()     

    # 💾 Zapisz dane po synchronizacji
    save_lottery_data()
    await load_kable_data(bot)

@tasks.loop(minutes=10)
async def aktualizuj_wiadomosci_loterii():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        await odswiez_loterie(guild)

from discord.ui import View, Button
from discord import app_commands
import discord
import json

active_capt_events = {}  # {guild_id: {"inicjator": user, "uczestnicy": set, "msg_id": id}}

class CaptView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.guild_id = interaction.guild.id
        self.inicjator = interaction.user
        active_capt_events[self.guild_id] = {
            "inicjator": self.inicjator,
            "uczestnicy": set([self.inicjator.id]),
            "msg_id": None
        }

    async def update_embed(self, interaction):
        capt = active_capt_events[self.guild_id]
        uczestnicy = capt["uczestnicy"]
        mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy)

        embed = discord.Embed(
            title="⚔️ CAPT Rozpoczęty",
            description=(
                f"👤 **Inicjator:** {capt['inicjator'].mention}\n"
                f"👥 **Uczestnicy ({len(uczestnicy)}):** {mentions}\n\n"
                f"📌 Za **capt wygrany** każdy uczestnik otrzymuje **2 pkt**, za **przegrany** – **1 pkt**."
            ),
            color=0xe74c3c
        )

        try:
            message = await interaction.channel.fetch_message(capt["msg_id"])
            await message.edit(embed=embed, view=self)
        except:
            pass

    @discord.ui.button(label="📥 Dołącz do CAPT", style=discord.ButtonStyle.primary)
    async def join(self, interaction: discord.Interaction, button: Button):
        capt = active_capt_events[self.guild_id]
        capt["uczestnicy"].add(interaction.user.id)
        await interaction.response.send_message("✅ Dołączyłeś do CAPT.", ephemeral=True)
        await self.update_embed(interaction)

    @discord.ui.button(label="📤 Opuść CAPT", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: Button):
        capt = active_capt_events[self.guild_id]
        if interaction.user.id in capt["uczestnicy"]:
            capt["uczestnicy"].remove(interaction.user.id)
            await interaction.response.send_message("🚪 Opuściłeś CAPT.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nie jesteś zapisany do CAPT.", ephemeral=True)
        await self.update_embed(interaction)

    @discord.ui.button(label="🏁 CAPT Wygrany", style=discord.ButtonStyle.success)
    async def win(self, interaction: discord.Interaction, button: Button):
        await self.zakonczenie(interaction, wygrana=True)

    @discord.ui.button(label="❌ CAPT Przegrany", style=discord.ButtonStyle.danger)
    async def lose(self, interaction: discord.Interaction, button: Button):
        await self.zakonczenie(interaction, wygrana=False)

    async def zakonczenie(self, interaction: discord.Interaction, wygrana: bool):
        capt = active_capt_events.get(self.guild_id)
        if not capt:
            return

        if interaction.user.id != capt["inicjator"].id:
            try:
                await interaction.response.send_message("❌ Tylko inicjator może zakończyć CAPT.", ephemeral=True)
            except discord.NotFound:
                pass
            return

        uczestnicy = capt["uczestnicy"]
        for uid in uczestnicy:
            str_uid = str(uid)
            init_user(str_uid)
            user_data[str_uid]["punkty"] += 2 if wygrana else 1
            user_data[str_uid]["capt"] += 1

        with open("dane.json", "w") as f:
            json.dump(user_data, f)

        wynik = "WYGRANY" if wygrana else "PRZEGRANY"
        kolor = 0x2ecc71 if wygrana else 0xe67e22
        mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy)

        embed = discord.Embed(
            title=f"🎯 CAPT {wynik}",
            description=(
                f"👤 **Inicjator:** {capt['inicjator'].mention}\n"
                f"👥 **Uczestnicy:** {mentions}\n\n"
                f"🏁 CAPT został zakończony jako **{wynik}**!\n"
                f"{'🥇 Każdy otrzymuje 2 pkt!' if wygrana else '🥈 Każdy otrzymuje 1 pkt!'}"
            ),
            color=kolor
        )

        try:
            message = await interaction.channel.fetch_message(capt["msg_id"])
            await message.edit(content="", embed=embed, view=None)
        except:
            await interaction.channel.send(embed=embed)

        del active_capt_events[self.guild_id]
        await odswiez_statystyki(interaction.guild)

@tree.command(name="capt", description="Rozpocznij akcję CAPT – tylko dla Leaderów i Zarządu")
@app_commands.checks.has_any_role("Leader", "Zarząd")
async def capt(interaction: discord.Interaction):
    view = CaptView(interaction)
    embed = discord.Embed(
        title="⚔️ CAPT Rozpoczęty",
        description=(
            f"👤 **Inicjator:** {interaction.user.mention}\n"
            f"👥 **Uczestnicy (1):** {interaction.user.mention}\n\n"
            f"📌 Za **capt wygrany** każdy uczestnik otrzymuje **2 pkt**, za **przegrany** – **1 pkt**."
        ),
        color=0xe74c3c
    )
    msg = await interaction.channel.send(embed=embed, view=view)
    active_capt_events[interaction.guild.id]["msg_id"] = msg.id
    await interaction.response.send_message("📢 CAPT rozpoczęty!", ephemeral=True)

@capt.error
async def capt_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingAnyRole):
        await interaction.response.send_message("❌ Tylko Leader lub Zarząd może rozpocząć CAPT.", ephemeral=True)

@tree.command(name="biuroall", description="Wyślij wezwanie na CAPT 5 razy co 5 sekund")
async def biuroall(interaction: discord.Interaction):
    await interaction.response.send_message("🔁 Wysyłam wezwanie do biura...", ephemeral=True)

    kanal = discord.utils.get(interaction.guild.text_channels, name="🗣┃zbiórka")

    if not kanal:
        await interaction.followup.send("❌ Nie znaleziono kanału 🗣┃zbiórka.", ephemeral=True)
        return

    for _ in range(5):
        await kanal.send("@everyone 🚨 **Rozpoczęliśmy CAPT!** Wszyscy obowiązkowo na zbiórkę do biura! 🏃‍♂️🏃‍♀️")
        await asyncio.sleep(5)

@tree.command(name="godzinachaosu", description="Zobacz kiedy przypada dzisiejsza Godzina Chaosu")
@app_commands.checks.has_any_role("Leader", "Zarząd")
async def godzinachaosu(interaction: discord.Interaction):
    global godzina_chaosu, aktywny_chaos

    if aktywny_chaos:
        await interaction.response.send_message("🌀 **Godzina Chaosu trwa właśnie teraz!**", ephemeral=True)
    elif godzina_chaosu:
        await interaction.response.send_message(
            f"🕒 Dzisiejsza **Godzina Chaosu** została zaplanowana na: **{godzina_chaosu.strftime('%H:%M')}**",
            ephemeral=True
        )
    else:
        await interaction.response.send_message("❗ Godzina Chaosu jeszcze nie została wylosowana.", ephemeral=True)

@godzinachaosu.error
async def godzinachaosu_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingAnyRole):
        await interaction.response.send_message(
            "❌ Tylko użytkownicy z rolą `Leader` lub `Zarząd` mogą sprawdzić Godzinę Chaosu.",
            ephemeral=True
        )

from discord.ext import tasks

import random
from discord.ext import tasks
from discord.ui import View, Button

lottery_participants = {}  # {guild_id: set(user_ids)}
lottery_messages = {}      # {guild_id: message_id}

class LotteryView(View):
    def __init__(self, guild_id, message_id=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.message_id = message_id

    @discord.ui.button(label="🎊 Weź Udział w Loterii", style=discord.ButtonStyle.success, custom_id="loteria_zapis")
    async def join_lottery(self, interaction: discord.Interaction, button: Button):
        uid = str(interaction.user.id)
        init_user(uid)

        if user_data[uid]["punkty"] < 3:
            await interaction.response.send_message(
                "❌ Musisz mieć minimum 3 punkty aktywności, aby wziąć udział w loterii.",
                ephemeral=True
            )
            return

        uczestnicy = lottery_participants.setdefault(self.guild_id, set())
        if interaction.user.id in uczestnicy:
            await interaction.response.send_message(
                f"⚠️ Już jesteś zapisany do loterii!\n"
                f"🎟️ Aktualna liczba uczestników: **{len(uczestnicy)}**",
                ephemeral=True
            )
            return

        uczestnicy.add(interaction.user.id)
        save_lottery_data()
        await interaction.response.send_message(
            f"✅ Zapisano do loterii! 🎉\n"
            f"🎟️ Obecna liczba uczestników: **{len(uczestnicy)}**", ephemeral=True
        )

        await odswiez_loterie(interaction.guild)
        
        @discord.ui.button(label="🔄 Resetuj Loterię", style=discord.ButtonStyle.danger)
        async def reset_loterii(self, interaction: discord.Interaction, button: Button):
            role_names = [role.name.lower() for role in interaction.user.roles]
            if "lider" not in role_names and "zarząd" not in role_names:
                await interaction.response.send_message("❌ Tylko Lider lub Zarząd może resetować loterię.", ephemeral=True)
                return
            await interaction.response.send_modal(ResetLoteriiModal(interaction, self))

    @discord.ui.button(label="🏁 Zakończ Loterię", style=discord.ButtonStyle.primary)
    async def finish_loteria(self, interaction: discord.Interaction, button: Button):
            role_names = [role.name.lower() for role in interaction.user.roles]
            if "lider" not in role_names and "zarząd" not in role_names:
                await interaction.response.send_message("❌ Tylko Lider lub Zarząd może zakończyć loterię.", ephemeral=True)
                return
            await interaction.response.send_modal(ZakonczenieLoteriiModal(interaction, self))

@tree.command(name="loteria", description="Utwórz wiadomość loterii z przyciskiem do zapisu")
async def loteria(interaction: discord.Interaction):
    kanal = discord.utils.get(interaction.guild.text_channels, name="🎰┃loteria")
    if not kanal:
        kanal = await interaction.guild.create_text_channel("🎰┃loteria")

    uczestnicy = lottery_participants.get(interaction.guild.id, set())
    mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy) if uczestnicy else "Brak zgłoszeń"

    # Oblicz czas do niedzieli 17:00
    teraz = datetime.now()
    dni_do_niedzieli = (6 - teraz.weekday()) % 7
    next_lottery_time = (teraz + timedelta(days=dni_do_niedzieli)).replace(hour=17, minute=0, second=0)
    if next_lottery_time < teraz:
        next_lottery_time += timedelta(days=7)
    czas_do = next_lottery_time - teraz
    godziny, reszta = divmod(czas_do.total_seconds(), 3600)
    minuty = int(reszta // 60)

    view = LotteryView(interaction.guild.id)
    msg = await kanal.send(
        "**🎰 LOTERIA TYGODNIOWA!**\n"
        "Kliknij przycisk poniżej, aby zapisać się do loterii!\n"
        "🔒 Wymagane minimum 3 punkty aktywności.\n"
        "🎁 Nagroda: 50k – losowanie w każdą niedzielę o 17:00!\n\n"
        f"⏳ **Do losowania pozostało:** {int(godziny)}h {minuty}min\n\n"
        f"📋 **Aktualni uczestnicy:**\n{mentions}",
        view=view
    )

    lottery_messages[interaction.guild.id] = msg.id
    await interaction.response.send_message("📨 Wiadomość loterii została wysłana!", ephemeral=True)

async def odswiez_loterie(guild):
    kanal = discord.utils.get(guild.text_channels, name="🎰┃loteria")
    if not kanal:
        return

    uczestnicy = lottery_participants.get(guild.id, set())
    mentions = ", ".join(f"<@{uid}>" for uid in uczestnicy) if uczestnicy else "Brak zgłoszeń"

    teraz = datetime.now()
    dni_do_niedzieli = (6 - teraz.weekday()) % 7
    next_lottery_time = (teraz + timedelta(days=dni_do_niedzieli)).replace(hour=17, minute=0, second=0)
    if next_lottery_time < teraz:
        next_lottery_time += timedelta(days=7)
    czas_do = next_lottery_time - teraz
    godziny, reszta = divmod(czas_do.total_seconds(), 3600)
    minuty = int(reszta // 60)

    msg_id = lottery_messages.get(guild.id)
    if not msg_id:
        return

    try:
        msg = await kanal.fetch_message(msg_id)
        view = LotteryView(guild.id, msg_id)
        await msg.edit(
            content=(
                "**🎰 LOTERIA TYGODNIOWA!**\n"
                "Kliknij przycisk poniżej, aby zapisać się do loterii!\n"
                "🔒 Wymagane minimum 3 punkty aktywności.\n"
                "🎁 Nagroda: 50k – losowanie w każdą niedzielę o 17:00!\n\n"
                f"⏳ **Do losowania pozostało:** {int(godziny)}h {minuty}min\n\n"
                f"📋 **Aktualni uczestnicy:**\n{mentions}"
            ),
            view=view
        )
    except Exception as e:
        print(f"[Błąd aktualizacji wiadomości loterii]: {e}")

@tasks.loop(minutes=1)
async def uruchom_loterie():
    teraz = datetime.now()
    if teraz.weekday() == 6 and teraz.hour == 17 and teraz.minute == 0:
        for guild in bot.guilds:
            kanal_loteria = discord.utils.get(guild.text_channels, name="🎰┃loteria")
            kanal_chat = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")

            uczestnicy = lottery_participants.get(guild.id, set())
            if not uczestnicy:
                if kanal_loteria:
                    await kanal_loteria.send("🎰 Loteria zakończona – brak uczestników spełniających wymagania.")
                continue

            zwyciezca = random.choice(list(uczestnicy))
            wynik = (
                f"🎉 **WYNIKI LOTERII!**\n"
                f"Nagrodę **50k** zgarnia: <@{zwyciezca}>!\n"
                f"Gratulacje i do zobaczenia za tydzień! 🤑"
            )

            if kanal_loteria:
                await kanal_loteria.send(wynik)
            if kanal_chat:
                await kanal_chat.send(f"@everyone {wynik}")

@tasks.loop(minutes=1)
async def przypomnienie_loteria():
    teraz = datetime.now()
    if teraz.hour == 16 and teraz.minute == 0:
        for guild in bot.guilds:
            kanal = discord.utils.get(guild.text_channels, name="💬┃chat-rodzinny")
            if not kanal:
                continue

            dni_do_niedzieli = (6 - teraz.weekday()) % 7
            next_lottery_time = (teraz + timedelta(days=dni_do_niedzieli)).replace(hour=17, minute=0, second=0)
            if next_lottery_time < teraz:
                next_lottery_time += timedelta(days=7)
            czas_do = next_lottery_time - teraz
            godziny, reszta = divmod(czas_do.total_seconds(), 3600)
            minuty = int(reszta // 60)

            await kanal.send(
                f"@everyone 🎰 **Loteria Tygodniowa** trwa!\n"
                f"📋 Do rozstrzygnięcia pozostało: **{int(godziny)}h {minuty}min**\n"
                f"Kliknij przycisk w kanale <#🎰┃loteria> i weź udział – jeśli masz minimum 3 punkty aktywności! 🍀💸"
            )

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

class WystawMnieView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.zgloszeni = {}  # user_id: datetime

    def format_lista(self):
        if not self.zgloszeni:
            return "*Brak zgłoszonych.*"
        lines = ["**📋 Aktualna lista zgłoszonych na capta:**\n"]
        for idx, (user_id, czas) in enumerate(self.zgloszeni.items(), start=1):
            user = self.bot.get_user(user_id)
            czas_str = czas.strftime("%H:%M")
            lines.append(f"`{idx}.` **{user.mention}** ⏰ {czas_str}")
        lines.append("\n❗ Możesz się wypisać tylko przez 15 minut od zgłoszenia.")
        lines.append("🔄 Lista aktualizuje się automatycznie.")
        return "\n".join(lines)

    async def update_message(self, interaction):
        embed = discord.Embed(title="🎯 Wystawienie na Capta", description=self.format_lista(), color=0x2ecc71)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="✅ Wystaw mnie", style=discord.ButtonStyle.success)
    async def wystaw(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.zgloszeni[interaction.user.id] = datetime.now()
        await self.update_message(interaction)
        await interaction.response.defer()

    @discord.ui.button(label="❌ Wypisz mnie", style=discord.ButtonStyle.danger)
    async def wypisz(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = datetime.now()
        zapisany = self.zgloszeni.get(interaction.user.id)

        if not zapisany:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=True)
            return

        if now - zapisany > timedelta(minutes=15):
            await interaction.response.send_message("Minęło ponad 15 minut od zgłoszenia. Nie możesz się już wypisać.", ephemeral=True)
            return

        del self.zgloszeni[interaction.user.id]
        await self.update_message(interaction)
        await interaction.response.defer()


class WystawMnie(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="wystawmnie", description="Zapisz się na capta z dynamiczną listą zgłoszeń")
    async def wystawmnie(self, interaction: discord.Interaction):
        view = WystawMnieView(self.bot)
        embed = discord.Embed(title="🎯 Wystawienie na Capta", description=view.format_lista(), color=0x2ecc71)
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(WystawMnie(bot))

@green.error
@blue.error
@white.error
async def cooldown_error(interaction: discord.Interaction, error):
    if isinstance(error, commands.CommandOnCooldown):
        remaining = round(error.retry_after)
        minutes, seconds = divmod(remaining, 60)
        await interaction.response.send_message(
            f"⏳ Możesz użyć tej komendy ponownie za **{int(minutes)}m {int(seconds)}s**.",
            ephemeral=True
        )

class ResetLoteriiModal(discord.ui.Modal, title="Reset Loterii"):
    kod = discord.ui.TextInput(label="Wpisz hasło", placeholder="np. LoteriaHeaven", required=True)

    def __init__(self, interaction, view):
        super().__init__()
        self.interaction = interaction
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        if self.kod.value != "LoteriaHeaven":
            await interaction.response.send_message("❌ Niepoprawny kod resetu.", ephemeral=True)
            return

        guild_id = self.interaction.guild.id
        user = interaction.user
        czas = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        lottery_participants[guild_id] = set()
        if guild_id in lottery_messages:
            del lottery_messages[guild_id]
        save_lottery_data()

        kanal = discord.utils.get(self.interaction.guild.text_channels, name="🎰┃loteria")
        if kanal:
            async for msg in kanal.history(limit=20):
                if msg.author == self.interaction.client.user:
                    await msg.delete()

        await loteria(self.interaction)
        await interaction.response.send_message("✅ Loteria została zresetowana!", ephemeral=True)

        print(f"[{czas}] 🔁 RESET LOTERII przez {user.name} ({user.id}) na serwerze {self.interaction.guild.name}")


import os
import asyncio

async def main():
    async with bot:
        await bot.load_extension("afk")  # załaduj cog afk.py
        await bot.start(os.getenv("DISCORD_TOKEN"))

asyncio.run(main())

