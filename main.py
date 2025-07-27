import discord
from discord.ext import commands
from discord import app_commands, Interaction
import json
import os

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)

BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
SETTINGS_FILE = "settings.json"

GOLD = ":g_:"
SILVER = ":s_:"
COPPER = ":c_:"

def load_json(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, "r") as f:
        return json.load(f)

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f)

def format_currency(value):
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{GOLD}{silver:02}{SILVER}{copper:02}{COPPER}"

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

@bot.event
async def on_guild_join(guild):
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            await channel.send(
                f"Hello! I'm the Currency Bot.
"
                f"Please use `/setup` to configure a channel and role for managing currency requests."
            )
            break

@bot.tree.command(name="setup", description="Configure approval channel and roles.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Channel to post requests", role="Role allowed to approve/give/take")
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role):
    settings = load_json(SETTINGS_FILE)
    settings[str(interaction.guild_id)] = {
        "channel_id": channel.id,
        "role_ids": [role.id]
    }
    save_json(SETTINGS_FILE, settings)
    await interaction.response.send_message(f"Setup complete. Using {channel.mention} and `{role.name}` for permissions.", ephemeral=True)

# Placeholder for the rest of the command set, to be appended after user confirms setup portion works
