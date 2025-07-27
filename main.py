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
intents.guilds = True
intents.members = True
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

def format_currency(amount, emoji):
    copper = amount % 100
    silver = (amount // 100) % 100
    gold = amount // 10000
    return f"{gold} {emoji['g']} {silver:02} {emoji['s']} {copper:02} {emoji['c']}"

def parse_amount(amount_str: str) -> int:
    """
    Convert a string like '12g34s56c' or '123456' into a raw integer amount in copper.
    Supports shorthand like '1g', '10s', '99c'.
    """
    if amount_str.isdigit():
        return int(amount_str)

    amount_str = amount_str.lower().replace(" ", "")
    total = 0
    num = ""
    for char in amount_str:
        if char.isdigit():
            num += char
        elif char in "gsc":
            if not num:
                raise ValueError(f"Missing number before '{char}'")
            if char == "g":
                total += int(num) * 10000
            elif char == "s":
                total += int(num) * 100
            elif char == "c":
                total += int(num)
            num = ""
    return total


@bot.tree.command(name="setup", description="Configure admin role, bot channel, request channel, and emoji formatting")
@commands.has_permissions(administrator=True)
async def setup(
    interaction: Interaction,
    bot_channel: discord.TextChannel,
    request_channel: discord.TextChannel,
    admin_role: discord.Role,
    gold: str = "g",
    silver: str = "s",
    copper: str = "c"
):
    guild_id = str(interaction.guild_id)
    config = load_json(CONFIG_FILE)

    config[guild_id] = {
        "admin_roles": [admin_role.id],
        "command_channel": bot_channel.id,
        "request_channel": request_channel.id,
        "emoji": {"g": gold, "s": silver, "c": copper}
    }

    save_json(CONFIG_FILE, config)

def log_transaction(guild_id, tx_type, user_id, amount, reason):
    transactions = load_json(HISTORY_FILE)
    transactions.append({
        "guild": guild_id,
        "type": tx_type,
        "user": user_id,
        "amount": amount,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat()
    })
    save_json(HISTORY_FILE, transactions)

@bot.tree.command(name="restore", description="Admin-only: Upload backup ZIP")
@commands.check(is_admin)
async def restore(interaction: Interaction, attachment: discord.Attachment):
    if not attachment.filename.endswith(".zip"):
        await interaction.response.send_message("⚠️ Upload a valid `.zip` backup file.", ephemeral=True)
        return

    file_path = f"/tmp/{attachment.filename}"
    await attachment.save(file_path)

    try:
        with zipfile.ZipFile(file_path, "r") as zipf:
            zipf.extractall()
        await interaction.response.send_message("✅ Restore completed successfully.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Restore failed: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@bot.tree.command(name="help", description="Show command list")
async def help_command(interaction: Interaction):
    await interaction.response.send_message(
        "**Available Commands:**\n"
        "`/balance [user]` – View your balance or another's\n"
        "`/transactions [user]` – View recent activity\n"
        "`/request <amount> <reason>` – Ask for money\n"
        "`/transfer <to> <amount> <reason>` – Ask to send money to someone\n"
        "\n**Admin Only:**\n"
        "`/give`, `/take` – Directly change balances\n"
        "`/balances` – View all users\n"
        "`/backup`, `/restore` – Export or restore data\n"
        "`/setup` – Configure bot channels, emojis, admin roles\n"
        "`/settings` – View current config"
    )


@bot.tree.command(name="balances", description="Admin-only: View all user balances")
@commands.check(is_admin)
async def balances(interaction: Interaction):
    guild_id = str(interaction.guild_id)
    balances_data = load_json(BALANCES_FILE).get(guild_id, {})
    config = load_json(CONFIG_FILE).get(guild_id, {})
    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})
    name_cache = load_json(NAME_CACHE_FILE).get(guild_id, {})

    if not balances_data:
        await interaction.response.send_message("No user balances found.", ephemeral=True)
        return

    lines = []
    for user_id, amount in sorted(balances_data.items(), key=lambda x: x[1], reverse=True):
        name = name_cache.get(user_id, f"<@{user_id}>")
        formatted = format_currency(amount, emoji)
        lines.append(f"{name}: {formatted}")

    pages = [lines[i:i+10] for i in range(0, len(lines), 10)]
    for i, page in enumerate(pages):
        await interaction.followup.send(
            f"📜 **Balances (Page {i+1}/{len(pages)}):**\n" + "\n".join(page),
            ephemeral=True
        )



@bot.tree.command(name="settings", description="View current settings")
async def settings_command(interaction: Interaction):
    guild_id = str(interaction.guild_id)
    config = load_json(CONFIG_FILE).get(guild_id, {})
    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})

    role_ids = config.get("admin_roles", [])
    role_mentions = [f"<@&{rid}>" for rid in role_ids] if role_ids else ["❓"]

    command_channel = config.get("command_channel")
    request_channel = config.get("request_channel")

    await interaction.response.send_message(
        f"**Settings for {interaction.guild.name}:**\n"
        f"Admin role(s): {', '.join(role_mentions)}\n"
        f"Command channel: {f'<#{command_channel}>' if command_channel else '❓'}\n"
        f"Request channel: {f'<#{request_channel}>' if request_channel else '❓'}\n"
        f"Emoji: {{'g': '{emoji['g']}', 's': '{emoji['s']}', 'c': '{emoji['c']}'}}"
    )

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

    emoji = load_json(CONFIG_FILE).get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

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

    emoji = load_json(CONFIG_FILE).get(guild_id, {}).get("emoji", {"g": "g", "s": "s", "c": "c"})

    await interaction.response.send_message(
        f"✅ Deducted {format_currency(delta, emoji)} from {user.mention}. "
        f"New balance: {format_currency(balances[guild_id][user_id], emoji)}"
    )
    log_transaction(guild_id, "admin-take", user_id, -delta, reason)

@bot.tree.command(name="balance", description="View your balance or another user's (admin only)")
async def balance(interaction: Interaction, user: Optional[discord.User] = None):
    guild_id = str(interaction.guild_id)
    balances = load_json(BALANCES_FILE).get(guild_id, {})
    config = load_json(CONFIG_FILE).get(guild_id, {})
    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})

    target_user = user or interaction.user
    target_id = str(target_user.id)

    if user and not is_admin(interaction):
        await interaction.response.send_message("❌ Only admins can check other users' balances.", ephemeral=True)
        return

    balance = balances.get(target_id, 0)
    formatted = format_currency(balance, emoji)

    await interaction.response.send_message(f"💰 Balance for {target_user.mention}: {formatted}")

@bot.tree.command(name="transactions", description="View your transactions or another user's (admin only)")
async def transactions(interaction: Interaction, user: Optional[discord.User] = None):
    guild_id = str(interaction.guild_id)
    history = load_json(HISTORY_FILE)
    config = load_json(CONFIG_FILE).get(guild_id, {})
    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})

    target_user = user or interaction.user
    target_id = str(target_user.id)

    if user and not is_admin(interaction):
        await interaction.response.send_message("❌ Only admins can view other users' history.", ephemeral=True)
        return

    txs = [tx for tx in history if tx["guild"] == guild_id and tx["user"] == target_id]
    if not txs:
        await interaction.response.send_message(f"No transactions found for {target_user.mention}.")
        return

    latest = sorted(txs, key=lambda x: x["timestamp"], reverse=True)[:10]
    lines = [f"`{tx['timestamp'][:19]}` | {tx['type']} | {format_currency(tx['amount'], emoji)} | {tx['reason']}" for tx in latest]

    await interaction.response.send_message(f"🧾 Last 10 transactions for {target_user.mention}:\n" + "\n".join(lines))

@bot.tree.command(name="rescan_requests", description="Admin: Repost missed currency or transfer requests")
@commands.check(is_admin)
async def rescan_requests(interaction: Interaction):
    guild_id = str(interaction.guild_id)
    config = load_json(CONFIG_FILE).get(guild_id, {})
    request_channel = config.get("request_channel")
    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})

    requests = load_json(REQUESTS_FILE)
    reposted = 0

    if not request_channel:
        await interaction.response.send_message("⚠️ Request channel not configured.", ephemeral=True)
        return

    channel = bot.get_channel(request_channel)
    if not channel:
        await interaction.response.send_message("⚠️ Couldn't find the request channel.", ephemeral=True)
        return

    for req in requests:
        # Skip if already approved/denied
        if "resolved" in req:
            continue

        try:
            if req["type"] == "transfer":
                embed = discord.Embed(
                    title="🔁 Transfer Request",
                    description=(f"From: <@{req['from']}>\nTo: <@{req['to']}>\n"
                                 f"Amount: {format_currency(parse_amount(req['amount']), emoji)}\n"
                                 f"Reason: {req['reason']}"),
                    timestamp=datetime.fromisoformat(req["timestamp"])
                )
            elif req["type"] == "request":
                embed = discord.Embed(
                    title="💰 Currency Request",
                    description=(f"User: <@{req['user']}>\n"
                                 f"Amount: {format_currency(parse_amount(req['amount']), emoji)}\n"
                                 f"Reason: {req['reason']}"),
                    timestamp=datetime.fromisoformat(req["timestamp"])
                )
            else:
                continue

            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            reposted += 1
        except Exception as e:
            continue

    await interaction.response.send_message(f"✅ Reposted {reposted} request(s) for review.")


@bot.tree.command(name="transfer", description="Request or perform a currency transfer")
async def transfer(interaction: Interaction, to: discord.User, amount: str, reason: str, from_user: Optional[discord.User] = None):
    guild_id = str(interaction.guild_id)
    config = load_json(CONFIG_FILE).get(guild_id, {})
    command_channel = config.get("command_channel")
    request_channel = config.get("request_channel")

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

    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})
    formatted_amount = format_currency(parse_amount(amount), emoji)

    channel = bot.get_channel(request_channel)
    msg = await channel.send(
        embed=discord.Embed(
            title="🔁 Transfer Request",
            description=(f"From: <@{req['from']}>\nTo: <@{req['to']}>\n"
                         f"Amount: {formatted_amount}\nReason: {reason}"),
            timestamp=datetime.utcnow()
        )
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Transfer request submitted for approval.", ephemeral=True)
@bot.tree.command(name="request", description="Request currency with reason")
async def request(interaction: Interaction, amount: str, reason: str):
    guild_id = str(interaction.guild_id)
    config = load_json(CONFIG_FILE).get(guild_id, {})

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

    emoji = config.get("emoji", {"g": "g", "s": "s", "c": "c"})
    formatted_amount = format_currency(parse_amount(amount), emoji)

    request_channel_id = config.get("request_channel")
    request_channel = bot.get_channel(request_channel_id)

    msg = await request_channel.send(
        embed=discord.Embed(
            title="💰 Currency Request",
            description=(f"User: {interaction.user.mention}\n"
                         f"Amount: {formatted_amount}\n"
                         f"Reason: {reason}"),
            timestamp=datetime.utcnow()
        )
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Request posted for approval.", ephemeral=True)
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} commands.")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

    config = load_json(CONFIG_FILE)
    name_cache = load_json(NAME_CACHE_FILE)

    for guild in bot.guilds:
        guild_id = str(guild.id)
        guild_cfg = config.get(guild_id)

        if not guild_cfg:
            print(f"⚠️ No config for {guild.name} ({guild_id})")
            channel = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
            if channel:
                await channel.send(
                    "**👋 Hello! I'm the TSC Payroll Bot!**\n"
                    "To get started, run `/setup` to configure command and request channels + admin role."
                )
            continue

        # Refresh missing name cache entries
        balances = load_json(BALANCES_FILE).get(guild_id, {})
        name_cache.setdefault(guild_id, {})
        for user_id in balances:
            if user_id not in name_cache[guild_id]:
                try:
                    user = await bot.fetch_user(int(user_id))
                    name_cache[guild_id][user_id] = user.name
                except:
                    continue

    save_json(NAME_CACHE_FILE, name_cache)

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

    try:
        channel = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if channel:
            await channel.send(
                f"👋 Thanks for adding me to **{guild.name}**!\n"
                f"Use `/setup` to get started with your admin role, command channel, and request channel."
            )
    except Exception as e:
        print(f"Failed to send welcome message: {e}")

# ✅ Run the bot
bot.run("YOUR_BOT_TOKEN")
