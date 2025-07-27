# === Part 1: Imports, Setup, Helpers ===

import discord
import io
from discord import File
from discord.ext import commands
from discord.ext.commands import check
from discord import app_commands, Interaction
import asyncio
import json
import os
import datetime
from datetime import datetime
import zipfile
import logging
from typing import Optional

logging.basicConfig(level=logging.INFO)

CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
HISTORY_FILE = "transactions.json"
NAME_CACHE_FILE = "name_cache.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_owner_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        app_info = await interaction.client.application_info()
        return interaction.user.id == app_info.owner.id
    return app_commands.check(predicate)

def is_admin(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild_id), {})
    allowed_roles = guild_cfg.get("admin_roles", [])
    return any(role.id in allowed_roles for role in interaction.user.roles)

def is_valid_command_channel(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild_id), {})
    allowed_channel = guild_cfg.get("command_channel")
    return allowed_channel == interaction.channel_id

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def format_currency(amount, guild_id=None):
    if guild_id:
        config = load_json(CONFIG_FILE)
        emojis = config.get(str(guild_id), {}).get("emoji", {"g": "g", "s": "s", "c": "c"})
    else:
        emojis = {"g": "g", "s": "s", "c": "c"}

    copper = amount % 100
    silver = (amount // 100) % 100
    gold = amount // 10000

    return f"{gold} {emojis['g']} {silver:02} {emojis['s']} {copper:02} {emojis['c']}"

@bot.event
async def on_ready():
    print(f"{bot.user} is now online.")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_guild_join(guild):
    config = load_json(CONFIG_FILE)
    if str(guild.id) not in config:
        config[str(guild.id)] = {
            "admin_roles": [],
            "command_channel": None,
            "request_channel": None,
            "emoji": {"g": "g", "s": "s", "c": "c"}
        }
        save_json(CONFIG_FILE, config)

@bot.tree.command(name="setup", description="Set command and request channels, admin role, and emoji config")
@commands.has_permissions(administrator=True)
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role,
                gold: str = "g", silver: str = "s", copper: str = "c"):
    config = load_json(CONFIG_FILE)
    if str(interaction.guild_id) not in config:
        config[str(interaction.guild_id)] = {}

    config[str(interaction.guild_id)].update({
        "admin_roles": [role.id],
        "command_channel": channel.id,
        "request_channel": channel.id,
        "emoji": {"g": gold, "s": silver, "c": copper}
    })
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"✅ Setup complete!\n"
        f"Requests will post in {channel.mention}.\n"
        f"Commands allowed only in {channel.mention}.\n"
        f"Admin role: {role.name}\n"
        f"Emojis: {gold} • {silver} • {copper}",
        ephemeral=False
    )

@bot.tree.command(name="backup", description="Backup all bot data")
@commands.check(is_admin)
async def backup_command(interaction: Interaction):
    import zipfile

    backup_name = f"backup_{interaction.guild_id}.zip"
    with zipfile.ZipFile(backup_name, 'w') as zipf:
        for file in [CONFIG_FILE, BALANCES_FILE, REQUESTS_FILE, HISTORY_FILE, NAME_CACHE_FILE]:
            if os.path.exists(file):
                zipf.write(file)

    await interaction.response.send_message(
        content="📦 Backup created. Here is your data:",
        file=discord.File(backup_name)
    )
    os.remove(backup_name)

@bot.tree.command(name="restore", description="Restore from a backup file")
@commands.check(is_admin)
async def restore(interaction: Interaction, attachment: discord.Attachment):
    import zipfile

    if not attachment.filename.endswith(".zip"):
        await interaction.response.send_message("⚠️ Please upload a valid `.zip` file.", ephemeral=True)
        return

    file_path = f"/tmp/{attachment.filename}"
    await attachment.save(file_path)

    try:
        with zipfile.ZipFile(file_path, 'r') as zipf:
            zipf.extractall()
        await interaction.response.send_message("✅ Restore successful.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Restore failed: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
@bot.tree.command(name="give", description="Admin-only: Grant currency to a user")
@commands.check(is_admin)
async def give(interaction: Interaction, user: discord.User, amount: str, reason: str):
    guild_id = str(interaction.guild_id)
    balances = load_json(BALANCES_FILE)
    user_id = str(user.id)
    delta = parse_amount(amount)

    balances.setdefault(guild_id, {}).setdefault(user_id, 0)
    balances[guild_id][user_id] += delta
    save_json(BALANCES_FILE, balances)

    config = load_json(CONFIG_FILE)
    emoji = config.get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    await interaction.response.send_message(
        f"✅ Granted {format_currency(delta, emoji)} to {user.mention}. "
        f"New balance: {format_currency(balances[guild_id][user_id], emoji)}"
    )
    log_transaction(guild_id, "admin-give", user_id, delta, reason)

@bot.tree.command(name="take", description="Admin-only: Remove currency from a user")
@commands.check(is_admin)
async def take(interaction: Interaction, user: discord.User, amount: str, reason: str):
    guild_id = str(interaction.guild_id)
    balances = load_json(BALANCES_FILE)
    user_id = str(user.id)
    delta = parse_amount(amount)

    balances.setdefault(guild_id, {}).setdefault(user_id, 0)
    balances[guild_id][user_id] -= delta
    save_json(BALANCES_FILE, balances)

    config = load_json(CONFIG_FILE)
    emoji = config.get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    await interaction.response.send_message(
        f"✅ Deducted {format_currency(delta, emoji)} from {user.mention}. "
        f"New balance: {format_currency(balances[guild_id][user_id], emoji)}"
    )
    log_transaction(guild_id, "admin-take", user_id, -delta, reason)

@bot.tree.command(name="transfer", description="Request or perform a currency transfer")
async def transfer(interaction: Interaction, to: discord.User, amount: str, reason: str, from_user: Optional[discord.User] = None):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild_id), {})
    command_channel = guild_cfg.get("command_channel")
    request_channel = guild_cfg.get("request_channel")

    if not is_valid_command_channel(interaction):
        await interaction.response.send_message("❌ Use this command in the bot commands channel.", ephemeral=True)
        return

    is_admin_user = is_admin(interaction)
    acting_user = from_user if (from_user and is_admin_user) else interaction.user

    if acting_user == to:
        await interaction.response.send_message("⚠️ You can't transfer currency to yourself.", ephemeral=True)
        return

    req = {
        "type": "transfer",
        "from": acting_user.id,
        "to": to.id,
        "amount": amount,
        "reason": reason,
        "by": interaction.user.id,
        "timestamp": datetime.utcnow().isoformat()
    }

    requests = load_json(REQUESTS_FILE)
    requests.append(req)
    save_json(REQUESTS_FILE, requests)

    emoji = guild_cfg.get("emoji", {"g": "g", "s": "s", "c": "c"})
    formatted_amount = format_currency(parse_amount(amount), emoji)

    channel = bot.get_channel(request_channel)
    msg = await channel.send(
        embed=discord.Embed(
            title="🔁 Transfer Request",
            description=(
                f"From: <@{req['from']}>\n"
                f"To: <@{req['to']}>\n"
                f"Amount: {formatted_amount}\n"
                f"Reason: {reason}"
            ),
            timestamp=datetime.utcnow()
        )
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Transfer request submitted for approval.", ephemeral=True)

@bot.tree.command(name="request", description="Request currency with reason")
async def request(interaction: Interaction, amount: str, reason: str):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild_id), {})

    if not is_valid_command_channel(interaction):
        await interaction.response.send_message("❌ Requests are only allowed in the designated command channel.", ephemeral=True)
        return

    req = {
        "type": "request",
        "user": interaction.user.id,
        "amount": amount,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat()
    }

    requests = load_json(REQUESTS_FILE)
    requests.append(req)
    save_json(REQUESTS_FILE, requests)

    emoji = guild_cfg.get("emoji", {"g": "g", "s": "s", "c": "c"})
    formatted_amount = format_currency(parse_amount(amount), emoji)

    request_channel_id = guild_cfg.get("request_channel")
    request_channel = bot.get_channel(request_channel_id)

    msg = await request_channel.send(
        embed=discord.Embed(
            title="💰 Currency Request",
            description=(
                f"User: {interaction.user.mention}\n"
                f"Amount: {formatted_amount}\n"
                f"Reason: {reason}"
            ),
            timestamp=datetime.utcnow()
        )
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Request posted for approval.", ephemeral=True)
@bot.tree.command(name="balance", description="Check your currency balance")
async def balance_command(interaction: Interaction, user: Optional[discord.User] = None):
    user = user or interaction.user
    guild_id = str(interaction.guild_id)
    balances = load_json(BALANCES_FILE)
    config = load_json(CONFIG_FILE)
    emoji = config.get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})
    balance = balances.get(guild_id, {}).get(str(user.id), 0)

    await interaction.response.send_message(f"💰 Balance for you: {format_currency(balance, emoji)}")

@bot.tree.command(name="balances", description="Admin-only: View all balances")
@commands.check(is_admin)
async def balances_command(interaction: Interaction):
    guild_id = str(interaction.guild_id)
    balances = load_json(BALANCES_FILE).get(guild_id, {})
    name_cache = load_json(NAME_CACHE_FILE).get(guild_id, {})
    config = load_json(CONFIG_FILE)
    emoji = config.get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    lines = [
        f"<@{uid}>: {format_currency(balance, emoji)}"
        for uid, balance in balances.items()
    ]
    await interaction.response.send_message("📊 All balances:\n" + "\n".join(lines))

@bot.tree.command(name="transactions", description="View recent transactions")
async def transactions(interaction: Interaction, user: Optional[discord.User] = None):
    user_id = str((user or interaction.user).id)
    guild_id = str(interaction.guild_id)
    history = load_json(HISTORY_FILE)
    config = load_json(CONFIG_FILE)
    emoji = config.get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    txns = [h for h in history if h["guild"] == guild_id and h["user"] == user_id]
    txns.sort(key=lambda x: x["timestamp"], reverse=True)

    lines = [
        f"{h['timestamp'][:19]}: {format_currency(h['amount'], emoji)} for {h['reason']}"
        for h in txns[:10]
    ] or ["No transactions found."]
    await interaction.response.send_message("🧾 Last 10 transactions:\n" + "\n".join(lines))

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    msg = reaction.message
    if msg.channel.id != load_json(CONFIG_FILE).get(str(msg.guild.id), {}).get("request_channel"):
        return

    if not is_admin(await msg.channel.guild.fetch_member(user.id)):
        return

    if str(reaction.emoji) not in ["✅", "❌"]:
        return

    requests = load_json(REQUESTS_FILE)
    found = next((r for r in requests if r.get("msg_id") == msg.id), None)
    if not found:
        return

    guild_id = str(msg.guild.id)
    balances = load_json(BALANCES_FILE)
    emoji = load_json(CONFIG_FILE).get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    if reaction.emoji == "✅":
        amt = parse_amount(found["amount"])
        if found["type"] == "request":
            user_id = str(found["user"])
            balances.setdefault(guild_id, {}).setdefault(user_id, 0)
            balances[guild_id][user_id] += amt
            log_transaction(guild_id, "approved-request", user_id, amt, found["reason"])
            await msg.channel.send(f"✅ Approved! {format_currency(amt, emoji)} granted to <@{user_id}>.")
        elif found["type"] == "transfer":
            from_id, to_id = str(found["from"]), str(found["to"])
            balances.setdefault(guild_id, {}).setdefault(from_id, 0)
            balances.setdefault(guild_id, {}).setdefault(to_id, 0)
            balances[guild_id][from_id] -= amt
            balances[guild_id][to_id] += amt
            log_transaction(guild_id, "approved-transfer", to_id, amt, found["reason"])
            log_transaction(guild_id, "approved-transfer", from_id, -amt, found["reason"])
            await msg.channel.send(
                f"✅ Approved! {format_currency(amt, emoji)} transferred from <@{from_id}> to <@{to_id}>."
            )

    else:
        await msg.channel.send("❌ Request denied.")

    requests.remove(found)
    save_json(REQUESTS_FILE, requests)
    save_json(BALANCES_FILE, balances)

@bot.tree.command(name="backup", description="Admin-only: Download all data")
@commands.check(is_admin)
async def backup_command(interaction: Interaction):
    zip_path = "/tmp/backup.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for f in [BALANCES_FILE, REQUESTS_FILE, HISTORY_FILE, CONFIG_FILE, NAME_CACHE_FILE]:
            z.write(f)
    await interaction.response.send_message("📦 Backup created:", file=discord.File(zip_path))

@bot.tree.command(name="restore", description="Admin-only: Upload backup ZIP")
@commands.check(is_admin)
async def restore(interaction: Interaction):
    await interaction.response.send_message("🔁 Send the backup zip file now.")

    def check(m):
        return m.author == interaction.user and m.attachments

    try:
        msg = await bot.wait_for("message", timeout=30.0, check=check)
        z = zipfile.ZipFile(await msg.attachments[0].read())
        for name in z.namelist():
            with open(name, "wb") as f:
                f.write(z.read(name))
        await interaction.followup.send("✅ Restore completed. Restart the bot.")
    except Exception as e:
        await interaction.followup.send(f"❌ Restore failed: {e}")

@bot.tree.command(name="help", description="Show command list")
async def help_command(interaction: Interaction):
    await interaction.response.send_message(
        "**Commands:**\n"
        "/balance [user]\n"
        "/transactions [user]\n"
        "/request <amount> <reason>\n"
        "/transfer <to> <amount> <reason>\n"
        "/setup (admin-only)\n"
        "/give, /take, /balances, /backup, /restore (admin-only)"
    )

@bot.tree.command(name="settings", description="Show current settings")
async def settings_command(interaction: Interaction):
    guild_id = str(interaction.guild_id)
    cfg = load_json(CONFIG_FILE).get(guild_id, {})
    await interaction.response.send_message(
        f"**Settings for {interaction.guild.name}:**\n"
        f"Admin role: {cfg.get('admin_role', '❓')}\n"
        f"Command channel: <#{cfg.get('command_channel', '❓')}>\n"
        f"Request channel: <#{cfg.get('request_channel', '❓')}>\n"
        f"Emoji: {cfg.get('emoji', {})}"
    )

bot.run(os.getenv("DISCORD_TOKEN"))
