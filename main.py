# === Part 1: Imports, Setup, Helpers ===

import discord
import io
from discord import File
from discord.ext import commands
from discord import app_commands, Interaction
import asyncio
import json
import os
import datetime
from datetime import datetime
import zipfile
import logging

logging.basicConfig(level=logging.INFO)

CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
HISTORY_FILE = "transactions.json"
NAME_CACHE_FILE = "name_cache.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

def is_owner_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        app_info = await interaction.client.application_info()
        return interaction.user.id == app_info.owner.id
    return app_commands.check(predicate)

def is_admin(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild.id), {})
    allowed_roles = guild_cfg.get("admin_roles", [])
    return any(role.id in allowed_roles for role in interaction.user.roles)

def is_valid_command_channel(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild.id), {})
    allowed_channel = guild_cfg.get("command_channel")
    return allowed_channel == interaction.channel_id

# Load or save JSON
def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# Format value as currency string
def format_currency(value, guild_id):
    config = load_json(CONFIG_FILE)
    emojis = config.get(str(guild_id), {}).get("emojis", {})
    g = emojis.get("gold", "g")
    s = emojis.get("silver", "s")
    c = emojis.get("copper", "c")
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{g} {silver:02}{s} {copper:02}{c}"
# === Bot Startup Events ===

@bot.event
async def on_ready():
    bot_name = "The Scribe's Cauldron Payroll Bot"
    print(f"✅ Logged in as {bot.user.name} | Renaming to {bot_name}")

    try:
        await bot.change_presence(activity=discord.Game(name=bot_name))
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"⚠️ Sync failed: {e}")

    config_exists = os.path.exists(CONFIG_FILE)
    config = load_json(CONFIG_FILE) if config_exists else {}

    for guild in bot.guilds:
        try:
            cfg = config.get(str(guild.id), {})
            channel_id = cfg.get("request_channel")
            channel = None

            if channel_id:
                channel = guild.get_channel(channel_id)
                if not channel:
                    channel = await bot.fetch_channel(channel_id)
            else:
                channel = guild.system_channel or discord.utils.get(guild.text_channels, name="general")

            if channel:
                if config_exists and str(guild.id) in config:
                    await channel.send("🔔 The Scribe's Cauldron Payroll Bot is now online and ready!")
                else:
                    await channel.send(
                        "⚠️ The bot has restarted and no configuration was found.\n"
                        "An admin must run `/setup` to reconfigure the bot or `/restore` to restore from a backup."
                    )
        except Exception as e:
            print(f"⚠️ Could not send startup message in {guild.name}: {e}")


@bot.event
async def on_guild_join(guild):
    print(f"➕ Joined new guild: {guild.name} ({guild.id})")
    try:
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None
        )
        if channel:
            await channel.send(
                "👋 Thanks for adding me! Use `/setup` to configure The Scribe's Cauldron Payroll Bot.\n"
                "Admins can define request + command channels and approval roles, or use `/restore` to import a backup."
            )
    except Exception as e:
        print(f"⚠️ Failed to send join message in {guild.name}: {e}")


@bot.tree.command(name="setup", description="Configure the bot for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    request_channel="Channel for currency requests",
    command_channel="Channel where bot commands are allowed",
    role="Admin role",
    gold="Gold emoji (optional)", silver="Silver emoji (optional)", copper="Copper emoji (optional)"
)
async def setup(
    interaction: Interaction,
    request_channel: discord.TextChannel,
    command_channel: discord.TextChannel,
    role: discord.Role,
    gold: str = "g", silver: str = "s", copper: str = "c"
):
    config = load_json(CONFIG_FILE)
    config[str(interaction.guild.id)] = {
        "request_channel": request_channel.id,
        "command_channel": command_channel.id,
        "admin_roles": [role.id],
        "emojis": {"gold": gold, "silver": silver, "copper": copper}
    }
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Setup complete!\n"
        f"Requests will post in {request_channel.mention}.\n"
        f"Commands allowed only in {command_channel.mention}.\n"
        f"Admin role: `{role.name}`\n"
        f"Emojis: {gold}, {silver}, {copper}"
    )
@bot.tree.command(name="backup", description="Admin only: download all config and data files.")
@app_commands.checks.has_permissions(administrator=True)
async def backup_command(interaction: discord.Interaction):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        zip_filename = f"currency_backup_{timestamp}.zip"

        with zipfile.ZipFile(zip_filename, 'w') as zipf:
            for file in [CONFIG_FILE, BALANCES_FILE, REQUESTS_FILE, HISTORY_FILE]:
                if os.path.exists(file):
                    zipf.write(file)

        backup_file = File(zip_filename)
        await interaction.response.send_message("📦 Backup file:", file=backup_file, ephemeral=True)
        os.remove(zip_filename)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to create backup: {e}", ephemeral=True)


@bot.tree.command(name="restore", description="Restore from a backup ZIP file.")
@is_owner_check()
async def restore(interaction: Interaction, file: discord.Attachment):
    await interaction.response.defer(thinking=True)

    if not file.filename.endswith(".zip"):
        await interaction.followup.send("🚫 Please upload a valid ZIP file.", ephemeral=True)
        return

    try:
        zip_bytes = await file.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zipf:
            for name in zipf.namelist():
                with zipf.open(name) as f:
                    with open(name, 'wb') as out_f:
                        out_f.write(f.read())
        await interaction.followup.send("✅ Restore complete.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Restore failed: {str(e)}", ephemeral=True)


@bot.tree.command(name="give", description="Admin-only: Grant currency to a user.")
@app_commands.describe(user="The user to give currency to", amount="Amount in copper", reason="Reason for the grant")
@is_admin()
async def give(interaction: Interaction, user: discord.Member, amount: int, reason: str):
    balances = load_json(BALANCES_FILE)
    uid = str(user.id)

    if amount <= 0:
        await interaction.response.send_message("🚫 Amount must be greater than 0.", ephemeral=True)
        return

    balances[uid] = balances.get(uid, 0) + amount
    save_json(BALANCES_FILE, balances)

    log = load_json(HISTORY_FILE)
    log.setdefault(uid, []).append({
        "type": "grant",
        "amount": amount,
        "reason": reason,
        "by": interaction.user.id
    })
    save_json(HISTORY_FILE, log)

    formatted_amount = format_currency(amount, interaction.guild.id)
    new_balance = format_currency(balances[uid], interaction.guild.id)
    await interaction.response.send_message(
        f"✅ Granted {formatted_amount} to {user.mention}. New balance: {new_balance}"
    )


@bot.tree.command(name="take", description="Admin: Remove currency from a user.")
@app_commands.describe(user="Target user", amount="Amount in copper", reason="Reason for deduction")
async def take(interaction: Interaction, user: discord.User, amount: int, reason: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=False)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = max(0, balances.get(uid, 0) - amount)
    save_json(BALANCES_FILE, balances)

    log = load_json(HISTORY_FILE)
    log.setdefault(uid, []).append({
        "type": "deduct",
        "amount": -amount,
        "reason": reason,
        "by": interaction.user.id
    })
    save_json(HISTORY_FILE, log)

    await interaction.response.send_message(
        f"✅ Deducted {format_currency(amount, interaction.guild.id)} from {user.mention}. "
        f"New balance: {format_currency(balances[uid], interaction.guild.id)}"
    )


@bot.tree.command(name="balance", description="Check your balance or another user's (admin only).")
@app_commands.describe(user="(Optional) Another user to check the balance of")
async def balance_command(interaction: Interaction, user: discord.User = None):
    try:
        config = load_json(CONFIG_FILE)
        cfg = config.get(str(interaction.guild.id))
        if cfg is None:
            await interaction.response.send_message("❌ No config found. Please run `/setup`.", ephemeral=True)
            return

        target = user or interaction.user
        is_self = (target.id == interaction.user.id)

        if not is_self:
            admin_roles = cfg.get("admin_roles", [])
            user_roles = [role.id for role in interaction.user.roles]
            if not any(rid in admin_roles for rid in user_roles):
                await interaction.response.send_message("❌ You are not authorized to view other users' balances.", ephemeral=True)
                return

        balances = load_json(BALANCES_FILE)
        uid = str(target.id)
        balance = balances.get(uid, 0)

        emotes = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
        gold = balance // 10000
        silver = (balance % 10000) // 100
        copper = balance % 100

        msg = (
            f"💰 Balance for {'you' if is_self else target.mention}: "
            f"{gold}{emotes['gold']} {silver:02}{emotes['silver']} {copper:02}{emotes['copper']}"
        )
        await interaction.response.send_message(msg)
    except Exception as e:
        print(f"[ERROR] /balance failed: {e}")
        await interaction.response.send_message("❌ An internal error occurred while processing your request.", ephemeral=True)
@bot.tree.command(name="balances", description="Admin only: View all user balances.")
@is_admin()
async def balances_command(interaction: Interaction):
    await interaction.response.defer(thinking=True)

    balances = load_json(BALANCES_FILE)
    config = load_json(CONFIG_FILE)
    cache = load_json(NAME_CACHE_FILE)
    cfg = config.get(str(interaction.guild.id), {})
    emotes = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})

    lines = []
    for uid, value in balances.items():
        if uid not in cache:
            try:
                member = await interaction.guild.fetch_member(int(uid))
                cache[uid] = member.display_name
            except:
                cache[uid] = f"User {uid}"
        name = cache[uid]
        gold = value // 10000
        silver = (value % 10000) // 100
        copper = value % 100
        lines.append(f"{name}: {gold}{emotes['gold']} {silver:02}{emotes['silver']} {copper:02}{emotes['copper']}")
    save_json(NAME_CACHE_FILE, cache)

    if not lines:
        await interaction.followup.send("No balances recorded yet.")
    else:
        msg = "\n".join(sorted(lines))
        await interaction.followup.send(f"📋 All balances:\n{msg}")


@bot.tree.command(name="request", description="Request a currency change (requires approval).")
@app_commands.describe(amount="Amount in copper (use negative for deduction)", reason="Reason for the change")
async def request(interaction: Interaction, amount: int, reason: str):
    config = load_json(CONFIG_FILE)
    balances = load_json(BALANCES_FILE)
    requests = load_json(REQUESTS_FILE)
    cfg = config.get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ Bot is not configured. Please run `/setup`.", ephemeral=True)
        return

    allowed_channel = cfg.get("request_channel")
    if interaction.channel.id != allowed_channel:
        await interaction.response.send_message("🚫 Requests are only allowed in the designated request channel.", ephemeral=True)
        return

    balances.setdefault(str(interaction.user.id), 0)
    req_id = str(random.randint(100000, 999999))
    requests[req_id] = {
        "user": interaction.user.id,
        "amount": amount,
        "reason": reason,
        "approved": None
    }
    save_json(REQUESTS_FILE, requests)

    emotes = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    formatted = format_currency(amount, interaction.guild.id)

    embed = discord.Embed(
        title="💰 Currency Change Request",
        description=f"{interaction.user.mention} requested {formatted}\n📄 Reason: {reason}",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Request ID: {req_id}")
    message = await interaction.channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    await interaction.response.send_message("✅ Request submitted for approval.", ephemeral=True)


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or not reaction.message.guild:
        return

    message = reaction.message
    if not message.embeds or not message.embeds[0].footer.text.startswith("Request ID: "):
        return

    req_id = message.embeds[0].footer.text.split(":")[1].strip()
    requests = load_json(REQUESTS_FILE)
    request_data = requests.get(req_id)
    if not request_data or request_data.get("approved") is not None:
        return

    config = load_json(CONFIG_FILE)
    cfg = config.get(str(message.guild.id))
    if not cfg:
        return

    admin_roles = cfg.get("admin_roles", [])
    member = message.guild.get_member(user.id)
    if not member or not any(role.id in admin_roles for role in member.roles):
        return

    if reaction.emoji == "✅":
        amount = request_data["amount"]
        target_id = str(request_data["user"])
        balances = load_json(BALANCES_FILE)
        balances[target_id] = balances.get(target_id, 0) + amount
        save_json(BALANCES_FILE, balances)

        history = load_json(HISTORY_FILE)
        history.setdefault(target_id, []).append({
            "type": "request",
            "amount": amount,
            "reason": request_data["reason"],
            "by": user.id
        })
        save_json(HISTORY_FILE, history)

        await message.channel.send(f"✅ Request {req_id} approved by {user.mention}.")
        requests[req_id]["approved"] = True
    elif reaction.emoji == "❌":
        await message.channel.send(f"❌ Request {req_id} denied by {user.mention}.")
        requests[req_id]["approved"] = False

    save_json(REQUESTS_FILE, requests)


@bot.tree.command(name="transfer", description="Request to transfer currency to another user.")
@app_commands.describe(to_user="User to receive the currency", amount="Amount in copper", reason="Reason for the transfer")
async def transfer(interaction: Interaction, to_user: discord.User, amount: int, reason: str):
    config = load_json(CONFIG_FILE)
    balances = load_json(BALANCES_FILE)
    cfg = config.get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ No config found. Please run `/setup`.", ephemeral=True)
        return

    allowed_channel = cfg.get("request_channel")
    if interaction.channel.id != allowed_channel:
        await interaction.response.send_message("🚫 Transfers must be requested in the designated request channel.", ephemeral=True)
        return

    from_user = interaction.user
    balances.setdefault(str(from_user.id), 0)
    if balances[str(from_user.id)] < amount:
        await interaction.response.send_message("🚫 Insufficient funds.", ephemeral=True)
        return

    req_id = str(random.randint(100000, 999999))
    requests = load_json(REQUESTS_FILE)
    requests[req_id] = {
        "user": from_user.id,
        "to_user": to_user.id,
        "amount": amount,
        "reason": reason,
        "type": "transfer",
        "approved": None
    }
    save_json(REQUESTS_FILE, requests)

    formatted = format_currency(amount, interaction.guild.id)
    embed = discord.Embed(
        title="💸 Transfer Request",
        description=(
            f"{from_user.mention} → {to_user.mention}\n"
            f"💰 Amount: {formatted}\n📄 Reason: {reason}"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Request ID: {req_id}")
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Transfer request submitted for approval.", ephemeral=True)
@bot.tree.command(name="transactions", description="View your recent transactions.")
@app_commands.describe(user="(Admin only) View transactions for another user")
async def transactions(interaction: Interaction, user: Optional[discord.User] = None):
    config = load_json(CONFIG_FILE)
    cfg = config.get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ Bot is not configured. Please run `/setup`.", ephemeral=True)
        return

    target = user or interaction.user
    if user and not await is_admin_check(interaction):
        await interaction.response.send_message("❌ Only admins can view other users' transactions.", ephemeral=True)
        return

    history = load_json(HISTORY_FILE).get(str(target.id), [])
    if not history:
        await interaction.response.send_message("ℹ️ No transactions found for this user.", ephemeral=True)
        return

    emotes = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    lines = []
    for entry in reversed(history[-10:]):
        amt = format_currency(entry["amount"], interaction.guild.id)
        kind = entry["type"]
        who = f" by <@{entry['by']}>" if "by" in entry else ""
        lines.append(f"• {kind}: {amt} – {entry['reason']}{who}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="help", description="Show help for currency commands.")
async def help_command(interaction: Interaction):
    await interaction.response.send_message(
        "**💰 Currency Bot Help**\n"
        "`/balance` – View your balance\n"
        "`/give`, `/take` – Admin only: adjust balances\n"
        "`/request` – Request a currency change (requires approval)\n"
        "`/transfer` – Request to send coins to another player\n"
        "`/transactions` – View your last 10 transactions\n"
        "`/balances` – Admin only: View all user balances\n"
        "`/setup` – Admin only: Configure the bot\n"
        "`/settings` – Admin only: View the current settings\n"
        "`/backup` and `/restore` – Admin only: Export or restore bot data",
        ephemeral=True
    )


@bot.tree.command(name="settings", description="View current bot settings.")
@is_admin()
async def settings_command(interaction: Interaction):
    config = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not config:
        await interaction.response.send_message("❌ Bot not configured. Use `/setup`.", ephemeral=True)
        return

    channel = f"<#{config['request_channel']}>" if config.get("request_channel") else "Not set"
    roles = ", ".join(f"<@&{r}>" for r in config.get("admin_roles", [])) or "None"
    emojis = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    await interaction.response.send_message(
        f"**Settings**\n"
        f"Request channel: {channel}\n"
        f"Admin roles: {roles}\n"
        f"Emojis: gold={emojis['gold']}, silver={emojis['silver']}, copper={emojis['copper']}",
        ephemeral=True
    )


def format_currency(amount: int, guild_id: int) -> str:
    config = load_json(CONFIG_FILE).get(str(guild_id), {})
    emotes = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    gold = amount // 10000
    silver = (amount % 10000) // 100
    copper = amount % 100
    return f"{gold}{emotes['gold']} {silver:02}{emotes['silver']} {copper:02}{emotes['copper']}"


def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

async def is_admin_check(interaction: Interaction) -> bool:
    config = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
    admin_roles = config.get("admin_roles", [])
    if interaction.user.guild_permissions.administrator:
        return True
    if not admin_roles:
        return False
    return any(role.id in admin_roles for role in interaction.user.roles)

bot.run(os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN"))
