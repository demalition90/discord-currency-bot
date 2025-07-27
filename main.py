
import discord
from discord.ext import commands
from discord import app_commands, Interaction
import asyncio
import json
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"

GOLD = ":g_:"
SILVER = ":s_:"
COPPER = ":c_:"

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

def format_currency(value):
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{GOLD}{silver:02}{SILVER}{copper:02}{COPPER}"

def is_admin(interaction: Interaction, config):
    admin_roles = config.get(str(interaction.guild.id), {}).get("admin_roles", [])
    return any(role.id in admin_roles for role in interaction.user.roles)

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
    channel = guild.system_channel
    if channel:
        await channel.send("👋 Thanks for adding me! An admin can now run `/setup` to finish configuration.")

@bot.tree.command(name="setup", description="Configure currency bot for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Channel to post requests", role="Role allowed to approve")
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role):
    config = load_json(CONFIG_FILE)
    config[str(interaction.guild.id)] = {
        "request_channel": channel.id,
        "admin_roles": [role.id],
    }
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"Setup complete. Requests will go to {channel.mention}, and `{role.name}` can approve them.")

@bot.tree.command(name="give", description="Admin: Give currency to user.")
@app_commands.describe(user="User to receive funds", amount="Amount in copper", reason="Why?")
async def give(interaction: Interaction, user: discord.User, amount: int, reason: str):
    config = load_json(CONFIG_FILE)
    if not is_admin(interaction, config):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = balances.get(uid, 0) + amount
    save_json(BALANCES_FILE, balances)

    await interaction.response.send_message(f"Granted {format_currency(amount)} to {user.mention}. Reason: {reason}")

@bot.tree.command(name="take", description="Admin: Remove currency from user.")
@app_commands.describe(user="User to deduct from", amount="Amount in copper", reason="Why?")
async def take(interaction: Interaction, user: discord.User, amount: int, reason: str):
    config = load_json(CONFIG_FILE)
    if not is_admin(interaction, config):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = max(0, balances.get(uid, 0) - amount)
    save_json(BALANCES_FILE, balances)

    await interaction.response.send_message(f"Deducted {format_currency(amount)} from {user.mention}. Reason: {reason}")

@bot.tree.command(name="transfer", description="Request to transfer currency to another user (needs approval).")
@app_commands.describe(to="Recipient", amount="Amount in copper", reason="Why are you sending this?")
async def transfer(interaction: Interaction, to: discord.User, amount: int, reason: str):
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
        return

    requests = load_json(REQUESTS_FILE)
    request_id = str(interaction.id)
    requests[request_id] = {
        "type": "transfer",
        "from": str(interaction.user.id),
        "to": str(to.id),
        "amount": amount,
        "reason": reason,
    }
    save_json(REQUESTS_FILE, requests)

    config = load_json(CONFIG_FILE)
    channel_id = config.get(str(interaction.guild.id), {}).get("request_channel")
    channel = interaction.guild.get_channel(channel_id)
    if not channel:
        await interaction.response.send_message("❌ Setup not complete or invalid request channel.", ephemeral=True)
        return

    embed = discord.Embed(title="Transfer Request", description=f"{interaction.user.mention} ➡️ {to.mention}
{format_currency(amount)}
Reason: {reason}", color=0x3498DB)
    embed.set_footer(text=f"Transfer | From: {interaction.user.id} | To: {to.id} | Amount: {amount}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Transfer request submitted for approval.", ephemeral=True)

# Run the bot
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    asyncio.run(bot.start(TOKEN))
