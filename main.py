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






@bot.tree.command(name="give", description="(Admin) Grant currency to a user")
@app_commands.check(lambda i: is_admin(i))
@app_commands.describe(user="User to give currency to", amount="Total amount in copper (e.g. 12345 = 1g 23s 45c)", reason="Reason for giving currency")
async def give(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    try:
        await interaction.response.defer(ephemeral=False, thinking=True)

        user_id = str(user.id)
        balances = load_json("balances.json")
        transactions = load_json("transactions.json")

        # Update balance
        current = balances.get(user_id, 0)
        new_balance = current + amount
        balances[user_id] = new_balance
        save_json("balances.json", balances)

        # Update transactions
        transactions.setdefault(user_id, []).insert(0, f"+{amount} — Grant ({reason})")
        transactions[user_id] = transactions[user_id][:10]
        save_json("transactions.json", transactions)

        # Safe follow-up
        await interaction.followup.send(
            f"✅ Granted {format_currency(amount, interaction.guild.id)} to {user.mention}. "
            f"New balance: {format_currency(new_balance, interaction.guild.id)}",
            ephemeral=False
        )

    except Exception as e:
        # If follow-up fails, send fallback error
        await interaction.followup.send(
            f"❌ Error during /give: `{e}`",
            ephemeral=True
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
    guild_config = config.get(str(interaction.guild.id))
    if not guild_config:
        await interaction.response.send_message("❌ No configuration found. Please run `/setup`.", ephemeral=True)
        return

    request_channel_id = guild_config.get("request_channel")
    if not request_channel_id:
        await interaction.response.send_message("🚫 No request channel configured. Admin must run `/setup`.", ephemeral=True)
        return

    is_admin_user = is_admin(interaction)
    if not is_admin_user and from_user.id != interaction.user.id:
        await interaction.response.send_message("❌ You can only request transfers from your own account.", ephemeral=True)
        return

    # Load request queue and store request
    reqs = load_json(REQUESTS_FILE)
    req_id = str(interaction.id)
    reqs[req_id] = {
        "type": "transfer",
        "from": str(from_user.id),
        "to": str(to_user.id),
        "amount": amount,
        "reason": reason
    }
    save_json(REQUESTS_FILE, reqs)

    # Build embed
    emojis = guild_config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    amount_str = format_currency(amount, interaction.guild.id)
    embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
    embed.add_field(name="From", value=from_user.mention, inline=True)
    embed.add_field(name="To", value=to_user.mention, inline=True)
    embed.add_field(name="Amount", value=amount_str, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Transfer | From: {from_user.id} | To: {to_user.id} | Amount: {amount}")

    # Send embed to request channel
    channel = interaction.guild.get_channel(request_channel_id)
    if not channel:
        try:
            channel = await interaction.guild.fetch_channel(request_channel_id)
        except Exception as e:
            await interaction.response.send_message("❌ Failed to find request channel.", ephemeral=True)
            return

    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

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



@bot.tree.command(name="rescan_requests", description="Admin: Repost any unprocessed requests (e.g., after a restart).")
async def rescan_requests(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    config = load_json(CONFIG_FILE)
    guild_config = config.get(str(interaction.guild.id))
    if not guild_config:
        await interaction.response.send_message("❌ Bot is not configured. Please run `/setup`.", ephemeral=True)
        return

    request_channel_id = guild_config.get("request_channel")
    emojis = guild_config.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    reqs = load_json(REQUESTS_FILE)

    if not reqs:
        await interaction.response.send_message("📭 No pending requests found.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(request_channel_id)
    if not channel:
        try:
            channel = await interaction.guild.fetch_channel(request_channel_id)
        except Exception as e:
            await interaction.response.send_message("❌ Could not fetch request channel.", ephemeral=True)
            return

    reposted = 0
    for key, data in reqs.items():
        try:
            if data["type"] == "request":
                user = await interaction.client.fetch_user(int(data["user_id"]))
                amount_str = format_currency(data["amount"], interaction.guild.id)
                embed = discord.Embed(
                    title="Currency Request",
                    description=f"{user.mention} is requesting {amount_str}\nReason: {data['reason']}",
                    color=0xF1C40F
                )
                embed.set_footer(text=f"Request | User: {data['user_id']} | Amount: {data['amount']}")

            elif data["type"] == "transfer":
                from_user = await interaction.client.fetch_user(int(data["from"]))
                to_user = await interaction.client.fetch_user(int(data["to"]))
                amount_str = format_currency(data["amount"], interaction.guild.id)
                embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
                embed.add_field(name="From", value=from_user.mention, inline=True)
                embed.add_field(name="To", value=to_user.mention, inline=True)
                embed.add_field(name="Amount", value=amount_str, inline=False)
                embed.add_field(name="Reason", value=data["reason"], inline=False)
                embed.set_footer(text=f"Transfer | From: {data['from']} | To: {data['to']} | Amount: {data['amount']}")

            else:
                continue  # Skip unknown types

            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            reposted += 1

        except Exception as e:
            print(f"[rescan_requests] Failed to repost a request: {e}")
            continue

    await interaction.response.send_message(f"🔄 Reposted {reposted} request(s).", ephemeral=True)




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
