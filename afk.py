import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime

afk_users = {}  # user_id: start_time

class AfkView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.message = None
        self.start_time = afk_users[user_id]

    @discord.ui.button(label="🛑 Zakończ Afkować", style=discord.ButtonStyle.red)
    async def end_afk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("To nie Ty jesteś w trybie AFK!", ephemeral=True)

        end_time = datetime.now()
        start_time = self.start_time
        duration = end_time - start_time
        del afk_users[self.user_id]

        await interaction.response.edit_message(embed=discord.Embed(
            title="✅ AFK zakończony",
            description=f"{interaction.user.mention} wrócił do gry!\n\n"
                        f"⏱ Czas AFK: {str(duration).split('.')[0]}",
            color=discord.Color.green()
        ), view=None)

class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_afk_embeds.start()

    def cog_unload(self):
        self.update_afk_embeds.cancel()

    @tasks.loop(seconds=60)
    async def update_afk_embeds(self):
        for view in self.bot.persistent_views:
            if isinstance(view, AfkView) and view.message:
                start = view.start_time
                now = datetime.now()
                duration = now - start
                embed = discord.Embed(
                    title="😴 Gracz jest AFK",
                    description=f"<@{view.user_id}> jest AFK od: {start.strftime('%H:%M')}\n"
                                f"⏱ Minęło: {str(duration).split('.')[0]}",
                    color=discord.Color.orange()
                )
                try:
                    await view.message.edit(embed=embed, view=view)
                except discord.HTTPException:
                    pass

    @app_commands.command(name="afk", description="Zaznacz się jako gracz AFK")
    async def afk(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        if user_id in afk_users:
            return await interaction.response.send_message("Już jesteś oznaczony jako AFK!", ephemeral=True)

        # 🔧 FIX: szybka odpowiedź do Discorda
        await interaction.response.defer(ephemeral=True)

        start_time = datetime.now()
        afk_users[user_id] = start_time
        view = AfkView(user_id)
        embed = discord.Embed(
            title="😴 Gracz jest AFK",
            description=f"{interaction.user.mention} jest AFK od: {start_time.strftime('%H:%M')}\n"
                        f"⏱ Minęło: 0:00:00",
            color=discord.Color.orange()
        )

        afk_channel = discord.utils.get(interaction.guild.text_channels, name="😴┃afk")
        if not afk_channel:
            return await interaction.followup.send("Nie mogę znaleźć kanału 😴┃afk!", ephemeral=True)

        message = await afk_channel.send(embed=embed, view=view)
        view.message = message
        self.bot.add_view(view)

        await interaction.followup.send("Zostałeś oznaczony jako AFK.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AFK(bot))
