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

def is_owner_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        app_info = await interaction.client.application_info()
        return interaction.user.id == app_info.owner.id
    return app_commands.check(predicate)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
HISTORY_FILE = "transactions.json"

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

# Admin role check
def is_admin(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild.id), {})
    allowed_roles = guild_cfg.get("admin_roles", [])
    return any(role.id in allowed_roles for role in interaction.user.roles)

# === Bot Startup Events ===

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"⚠️ Sync failed: {e}")

import os  # Add this at the top of your file if not already imported

# === Bot Startup Events ===

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user.name}")
    try:
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
                # fallback if no config: use system channel or #general
                channel = guild.system_channel or discord.utils.get(guild.text_channels, name="general")

            if channel:
                if config_exists and str(guild.id) in config:
                    await channel.send("🔔 Currency bot is now online and ready!")
                else:
                    await channel.send(
                        "⚠️ Currency bot has restarted and no configuration was found.\n"
                        "An admin must run `/setup` to reconfigure the bot. or /restore to restore lost data using a backup file"
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
                "👋 Thanks for adding me! Use `/setup` to configure the currency bot.\n"
                "If you're an admin, run `/setup` to define which role can approve requests and which channel to use. Alternatively use /restore if you have a backup file"
            )
    except Exception as e:
        print(f"⚠️ Failed to send join message in {guild.name}: {e}")

@bot.tree.command(name="setup", description="Configure the bot for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Channel for request posts", role="Admin role", gold="Gold emoji (optional)", silver="Silver emoji (optional)", copper="Copper emoji (optional)")
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role, gold: str = "g", silver: str = "s", copper: str = "c"):
    config = load_json(CONFIG_FILE)
    config[str(interaction.guild.id)] = {
        "request_channel": channel.id,
        "admin_roles": [role.id],
        "emojis": {"gold": gold, "silver": silver, "copper": copper}
    }
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(f"✅ Setup complete!\nRequests will go to {channel.mention}.\nAdmin role: `{role.name}`\nEmojis: 🪙 {gold} • {silver} • {copper}")



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




@bot.tree.command(name="restore", description="Bot owner only: Restore data from a zip backup.")
@app_commands.check(is_owner_check())
async def restore_command(interaction: discord.Interaction, file: discord.Attachment):
    try:
        data = await file.read()
        with zipfile.ZipFile(io.BytesIO(data), 'r') as zip_ref:
            zip_ref.extractall()

        await interaction.response.send_message("✅ Backup restored successfully.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to restore backup: {e}", ephemeral=True)





@bot.tree.command(name="give", description="Admin: Grant currency to a user.")
@app_commands.describe(user="Recipient", amount="Amount in copper", reason="Reason for grant")
async def give(interaction: Interaction, user: discord.User, amount: int, reason: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=False)
        return

    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    balances[uid] = balances.get(uid, 0) + amount
    save_json(BALANCES_FILE, balances)

    log = load_json(HISTORY_FILE)
    log.setdefault(uid, []).append({"type": "grant", "amount": amount, "reason": reason, "by": interaction.user.id})
    save_json(HISTORY_FILE, log)

    await interaction.response.send_message(f"✅ Granted {format_currency(amount, interaction.guild.id)} to {user.mention}. New balance: {format_currency(balances[uid], interaction.guild.id)}")

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
    log.setdefault(uid, []).append({"type": "deduct", "amount": -amount, "reason": reason, "by": interaction.user.id})
    save_json(HISTORY_FILE, log)

    await interaction.response.send_message(f"✅ Deducted {format_currency(amount, interaction.guild.id)} from {user.mention}. New balance: {format_currency(balances[uid], interaction.guild.id)}")

@bot.tree.command(name="balance", description="Check your balance or another user's (admin only).")
@app_commands.describe(user="(Optional) Another user to check the balance of")
async def balance_command(interaction: Interaction, user: discord.User = None):
    try:
        # Load config and validate
        config = load_json(CONFIG_FILE)
        cfg = config.get(str(interaction.guild.id))
        if cfg is None:
            await interaction.response.send_message("❌ No config found. Please run `/setup`.", ephemeral=True)
            return

        # Who are we checking?
        target = user or interaction.user
        is_self = (target.id == interaction.user.id)

        # Check admin permissions if viewing another user
        if not is_self:
            admin_roles = cfg.get("admin_roles", [])
            user_roles = [role.id for role in interaction.user.roles]
            print(f"[DEBUG] Admin roles: {admin_roles}")
            print(f"[DEBUG] User roles: {user_roles}")
            if not any(rid in admin_roles for rid in user_roles):
                await interaction.response.send_message("❌ You are not authorized to view other users' balances.", ephemeral=True)
                return

        # Load balances and format
        balances = load_json(BALANCES_FILE)
        uid = str(target.id)
        balance = balances.get(uid, 0)

        gold = balance // 10000
        silver = (balance % 10000) // 100
        copper = balance % 100

        emotes = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})

        msg = (
            f"💰 Balance for {'you' if is_self else target.mention}: "
            f"{gold}{emotes['gold']} {silver:02}{emotes['silver']} {copper:02}{emotes['copper']}"
        )
        await interaction.response.send_message(msg)

    except Exception as e:
        print(f"[ERROR] /balance failed: {e}")
        await interaction.response.send_message("❌ An internal error occurred while processing your request.", ephemeral=True)


NAME_CACHE_FILE = "name_cache.json"

@bot.tree.command(name="balances", description="Admin only: view all user balances.")
@app_commands.checks.has_permissions(administrator=True)
async def balances_command(interaction: discord.Interaction):
    try:
        balances = load_json(BALANCES_FILE)
        config = load_json(CONFIG_FILE)
        emojis = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
        name_cache = {}

        if not balances:
            await interaction.response.send_message("📊 No balances found.", ephemeral=True)
            return

        msg = "**📊 All User Balances:**\n"
        for user_id, balance in balances.items():
            if user_id not in name_cache:
                try:
                    user = await interaction.client.fetch_user(int(user_id))
                    name_cache[user_id] = user.name
                except Exception:
                    name_cache[user_id] = f"User {user_id}"
            name = name_cache[user_id]
            msg += f"{name}: {format_currency(balance, emojis)}\n"

        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to load balances: {e}", ephemeral=True)



@bot.tree.command(name="request", description="Request currency from the server.")
@app_commands.describe(amount="Amount in copper", reason="Reason for request")
async def request(interaction: Interaction, amount: int, reason: str):
    reqs = load_json(REQUESTS_FILE)
    req_id = str(interaction.id)
    user_id = str(interaction.user.id)

    reqs[req_id] = {
        "type": "request",
        "user_id": user_id,
        "amount": amount,
        "reason": reason,
    }
    save_json(REQUESTS_FILE, reqs)

    config = load_json(CONFIG_FILE)
    req_channel_id = config.get(str(interaction.guild.id), {}).get("request_channel")
    channel = interaction.guild.get_channel(req_channel_id)
    if not channel:
        await interaction.response.send_message("❌ Request channel not configured.", ephemeral=False)
        return

    embed = discord.Embed(title="Currency Request", description=f"{interaction.user.mention} is requesting {format_currency(amount, interaction.guild.id)}\nReason: {reason}", color=0xF1C40F)
    embed.set_footer(text=f"Request | User: {user_id} | Amount: {amount}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("📝 Your request has been submitted for approval.", ephemeral=False)

@bot.tree.command(name="transfer", description="Request a currency transfer from one user to another.")
@app_commands.describe(
    from_user="User sending the currency",
    to_user="User receiving the currency",
    amount="Amount in copper",
    reason="Reason for transfer"
)
async def transfer_command(
    interaction: discord.Interaction,
    from_user: discord.User,
    to_user: discord.User,
    amount: int,
    reason: str
):
    config = load_json(CONFIG_FILE)
    request_channel_id = config.get("request_channel")
    admin_roles = config.get("admin_roles", [])

    if not is_admin(interaction) and from_user.id != interaction.user.id:
        await interaction.response.send_message("❌ You can only request transfers from your own account.", ephemeral=True)
        return

    if not request_channel_id:
        await interaction.response.send_message("❌ No request channel configured. Admin must run `/setup`.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(int(request_channel_id)) or await interaction.guild.fetch_channel(int(request_channel_id))
    emojis = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    amount_str = format_currency(amount, emojis)

    embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
    embed.add_field(name="From", value=from_user.mention, inline=True)
    embed.add_field(name="To", value=to_user.mention, inline=True)
    embed.add_field(name="Amount", value=amount_str, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Request | From: {from_user.id} | To: {to_user.id} | Amount: {amount}")

    message = await channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    await interaction.response.send_message("📨 Transfer request submitted for approval.", ephemeral=True)


@bot.tree.command(name="transactions", description="View your recent transactions.")
@app_commands.describe(user="User to view (admin only)")
async def transactions_command(interaction: discord.Interaction, user: discord.User = None):
    if user and not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to view other users' transactions.", ephemeral=True)
        return

    user_id = str(user.id if user else interaction.user.id)
    history = load_json(HISTORY_FILE)
    config = load_json(CONFIG_FILE)
    emojis = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})

    user_history = history.get(user_id, [])
    if not user_history:
        await interaction.response.send_message("📜 No transaction history found.", ephemeral=True)
        return

    msg = "**📜 Your last 10 transactions:**\n"
    for entry in reversed(user_history[-10:]):
        sign = "+" if entry["type"] == "grant" else "-"
        amount_str = format_currency(entry["amount"], emojis)
        msg += f"{sign}{amount_str} — {entry['type'].capitalize()} ({entry['reason']})\n"

    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="settings", description="Show the current bot config for this server.")
async def settings(interaction: Interaction):
    config = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not config:
        await interaction.response.send_message("❌ No config found. Please run /setup.", ephemeral=False)
        return

    chan = interaction.guild.get_channel(config["request_channel"])
    roles = [interaction.guild.get_role(rid) for rid in config.get("admin_roles", [])]
    emoji = config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})

    msg = f"""
📥 Request Channel: {chan.mention if chan else 'Unknown'}
🔑 Admin Roles: {', '.join(r.name for r in roles if r)}
💰 Emojis: {emoji['gold']} {emoji['silver']} {emoji['copper']}
"""
    await interaction.response.send_message(msg.strip(), ephemeral=False)

@bot.tree.command(name="help", description="Show usage and commands.")
async def help_command(interaction: Interaction):
    await interaction.response.send_message("""🧾 **Currency Bot Commands**
- `/balance` — Check your balance
- `/request` — Request currency (admins approve)
- `/transfer` — Request to send currency to another user
- `/transactions` — View your history
- `/setup` — (Admin) Configure the bot
- `/give` and `/take` — (Admin) Grant or remove currency
- `/rescan_requests` — (Admin) Repost missed requests
- `/settings` — View config info""", ephemeral=False)
@bot.tree.command(name="refresh", description="Admin: Force re-sync of slash commands.")
async def refresh(interaction: Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=False)
        return
    try:
        synced = await bot.tree.sync()
        await interaction.response.send_message(f"🔁 Synced {len(synced)} commands.", ephemeral=False)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Sync failed: {e}", ephemeral=False)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    if str(payload.emoji) not in ("✅", "❌"):
        return

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    if not message.embeds:
        return

    embed = message.embeds[0]
    footer = embed.footer.text

    reqs = load_json(REQUESTS_FILE)
    balances = load_json(BALANCES_FILE)
    config = load_json(CONFIG_FILE)
    history = load_json(HISTORY_FILE)

    guild_id = str(message.guild.id)
    emoji_format = config.get(guild_id, {}).get("emojis", {})
    gold = emoji_format.get("gold", "g")
    silver = emoji_format.get("silver", "s")
    copper = emoji_format.get("copper", "c")

    def emoji_value(value):
        g = value // 10000
        s = (value % 10000) // 100
        c = value % 100
        return f"{g}{gold}{s:02}{silver}{c:02}{copper}"

    if footer.startswith("Request"):
        uid = footer.split("User: ")[1].split(" |")[0]
        amount = int(footer.split("Amount: ")[1])
        for key, data in list(reqs.items()):
            if data["type"] == "request" and data["user_id"] == uid and data["amount"] == amount:
                if str(payload.emoji) == "✅":
                    balances[uid] = balances.get(uid, 0) + amount
                    history.setdefault(uid, []).append({"type": "request", "amount": amount, "reason": data["reason"], "by": "approval"})
                    await channel.send(f"✅ Approved {emoji_value(amount)} to <@{uid}>. New balance: {emoji_value(balances[uid])}")
                else:
                    await channel.send(f"❌ Denied request by <@{uid}>.")
                del reqs[key]
                break

    elif footer.startswith("Transfer"):
        from_id = footer.split("From: ")[1].split(" |")[0]
        to_id = footer.split("To: ")[1].split(" |")[0]
        amount = int(footer.split("Amount: ")[1])
        for key, data in list(reqs.items()):
            if data["type"] == "transfer" and data["from"] == from_id and data["to"] == to_id and data["amount"] == amount:
                if str(payload.emoji) == "✅":
                    if balances.get(from_id, 0) >= amount:
                        balances[from_id] -= amount
                        balances[to_id] = balances.get(to_id, 0) + amount
                        history.setdefault(from_id, []).append({"type": "transfer_out", "amount": -amount, "reason": data["reason"], "by": to_id})
                        history.setdefault(to_id, []).append({"type": "transfer_in", "amount": amount, "reason": data["reason"], "by": from_id})
                        await channel.send(f"✅ Transfer approved! <@{from_id}> ➡️ <@{to_id}> {emoji_value(amount)}")
                    else:
                        await channel.send(f"❌ Transfer failed: <@{from_id}> doesn't have enough funds.")
                else:
                    await channel.send(f"❌ Transfer denied for <@{from_id}>.")
                del reqs[key]
                break

    save_json(REQUESTS_FILE, reqs)
    save_json(BALANCES_FILE, balances)
    save_json(HISTORY_FILE, history)

bot.run(os.getenv("DISCORD_TOKEN"))
