
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
ADMIN_ROLE_NAME = "banker"

def load_json(file):
    if not os.path.exists(file):
        return {}
    with open(file, "r") as f:
        return json.load(f)

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

def format_currency(value, guild=None):
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100

    if guild:
        g = discord.utils.get(guild.emojis, name="g_")
        s = discord.utils.get(guild.emojis, name="s_")
        c = discord.utils.get(guild.emojis, name="c_")
        return f"{gold}{g or ':g_:'}{silver:02}{s or ':s_:'}{copper:02}{c or ':c_:'}"
    return f"{gold}:g_:{silver:02}:s_:{copper:02}:c_:"

def is_admin(interaction: discord.Interaction):
    return any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(e)

@bot.tree.command(name="balance")
async def balance(interaction: discord.Interaction):
    balances = load_json(BALANCES_FILE)
    user_id = str(interaction.user.id)
    amount = balances.get(user_id, 0)
    await interaction.response.send_message(f"{interaction.user.mention} has {format_currency(amount, interaction.guild)}")

@bot.tree.command(name="give")
@app_commands.describe(user="User to give currency to", amount="Amount (in copper)", reason="Reason for the grant")
async def give(interaction: discord.Interaction, user: discord.User, amount: int, reason: str):
    if not is_admin(interaction):
        await interaction.response.send_message("You don’t have permission to use this command.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = balances.get(uid, 0) + amount
    save_json(BALANCES_FILE, balances)

    await interaction.response.send_message(f"Granted {format_currency(amount, interaction.guild)} to {user.mention}.
Reason: {reason}")

@bot.tree.command(name="take")
@app_commands.describe(user="User to take currency from", amount="Amount (in copper)", reason="Reason for deduction")
async def take(interaction: discord.Interaction, user: discord.User, amount: int, reason: str):
    if not is_admin(interaction):
        await interaction.response.send_message("You don’t have permission to use this command.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = max(0, balances.get(uid, 0) - amount)
    save_json(BALANCES_FILE, balances)

    await interaction.response.send_message(f"Deducted {format_currency(amount, interaction.guild)} from {user.mention}.
Reason: {reason}")

@bot.tree.command(name="transfer")
@app_commands.describe(to="User to transfer to", amount="Amount (in copper)", reason="Reason for the transfer")
async def transfer(interaction: discord.Interaction, to: discord.User, amount: int, reason: str):
    requests = load_json(REQUESTS_FILE)
    req_id = str(interaction.id)
    requests[req_id] = {"user_id": str(interaction.user.id), "to_id": str(to.id), "amount": amount, "reason": reason}
    save_json(REQUESTS_FILE, requests)

    embed = discord.Embed(title="Transfer Request",
                          description=f"{interaction.user.mention} wants to transfer {format_currency(amount, interaction.guild)} to {to.mention}
Reason: {reason}",
                          color=0xF1C40F)
    embed.set_footer(text=f"Transfer | From: {interaction.user.id} | To: {to.id} | Amount: {amount}")
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("Your transfer request has been submitted.", ephemeral=True)

@bot.tree.command(name="rescan_requests")
async def rescan_requests(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Only admins can use this.", ephemeral=True)
        return

    requests = load_json(REQUESTS_FILE)
    count = 0
    for rid, req in list(requests.items()):
        from_id, to_id = req["user_id"], req.get("to_id")
        amount, reason = req["amount"], req.get("reason", "N/A")

        desc = f"<@{from_id}> requests {format_currency(amount, interaction.guild)}"
        if to_id:
            desc = f"<@{from_id}> → <@{to_id}>: {format_currency(amount, interaction.guild)}"

        embed = discord.Embed(title="Pending Request", description=f"{desc}
Reason: {reason}", color=0xF1C40F)
        embed.set_footer(text=f"Request ID: {rid} | From: {from_id} | To: {to_id} | Amount: {amount}")
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        count += 1

    await interaction.response.send_message(f"Rescanned {count} pending requests.", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id or str(payload.emoji) not in ["✅", "❌"]:
        return

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    if not member or all(role.name != ADMIN_ROLE_NAME for role in member.roles):
        return

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    if not message.embeds:
        return

    embed = message.embeds[0]
    footer = embed.footer.text or ""
    try:
        parts = {k.strip(): v.strip() for k, v in (kv.split(":") for kv in footer.split("|"))}
        from_id = parts.get("From")
        to_id = parts.get("To")
        amount = int(parts.get("Amount"))
        requests = load_json(REQUESTS_FILE)

        if str(payload.emoji) == "✅":
            balances = load_json(BALANCES_FILE)
            if to_id:
                balances[to_id] = balances.get(to_id, 0) + amount
                balances[from_id] = max(0, balances.get(from_id, 0) - amount)
                await channel.send(f"✅ Transfer approved: {format_currency(amount, guild)} from <@{from_id}> to <@{to_id}>.")
            else:
                balances[from_id] = balances.get(from_id, 0) + amount
                await channel.send(f"✅ Request approved: {format_currency(amount, guild)} granted to <@{from_id}>.")

            save_json(BALANCES_FILE, balances)
        else:
            await channel.send(f"❌ Request denied.")

        del requests[next(k for k, v in requests.items() if v['user_id'] == from_id and v['amount'] == amount)]
        save_json(REQUESTS_FILE, requests)

    except Exception as e:
        print("Reaction handler error:", e)

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    asyncio.run(bot.start(TOKEN))
