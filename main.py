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
HISTORY_FILE = "transactions.json"

# Helper: Load or save JSON
def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# Format value into currency string
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

# Check if user is admin
def is_admin(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild.id), {})
    allowed_roles = guild_cfg.get("admin_roles", [])
    return any(role.id in allowed_roles for role in interaction.user.roles)

# On bot startup
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"⚠️ Sync failed: {e}")

# On bot added to server
@bot.event
async def on_guild_join(guild):
    if guild.system_channel:
        await guild.system_channel.send(
            "👋 Thanks for adding me! An admin can now run `/setup` to finish configuration."
        )
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
    await interaction.response.send_message(f"✅ Setup complete!\nRequests will go to {channel.mention}.\nAdmin role: `{role.name}`.\nEmojis: {gold}, {silver}, {copper}")

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

@bot.tree.command(name="balance", description="Check your currency balance.")
async def balance(interaction: Interaction):
    balances = load_json(BALANCES_FILE)
    uid = str(interaction.user.id)
    current = balances.get(uid, 0)
    await interaction.response.send_message(f"💰 Your balance: {format_currency(current, interaction.guild.id)}", ephemeral=False)
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

@bot.tree.command(name="rescan_requests", description="Repost any unapproved requests.")
async def rescan_requests(interaction: Interaction):
    config = load_json(CONFIG_FILE)
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=False)
        return

    reqs = load_json(REQUESTS_FILE)
    posted = 0
    channel_id = config.get(str(interaction.guild.id), {}).get("request_channel")
    channel = interaction.guild.get_channel(channel_id)

    for req_id, data in reqs.items():
        if data["type"] == "request":
            user_id = data["user_id"]
            amount = data["amount"]
            reason = data.get("reason", "N/A")
            embed = discord.Embed(title="Currency Request", description=f"<@{user_id}> is requesting {format_currency(amount, interaction.guild.id)}\nReason: {reason}", color=0xF1C40F)
            embed.set_footer(text=f"Request | User: {user_id} | Amount: {amount}")
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            posted += 1
        elif data["type"] == "transfer":
            from_id = data["from"]
            to_id = data["to"]
            amount = data["amount"]
            reason = data.get("reason", "N/A")
            embed = discord.Embed(title="Transfer Request", description=f"<@{from_id}> ➡️ <@{to_id}>\n{format_currency(amount, interaction.guild.id)}\nReason: {reason}", color=0x3498DB)
            embed.set_footer(text=f"Transfer | From: {from_id} | To: {to_id} | Amount: {amount}")
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            posted += 1

    await interaction.response.send_message(f"🔁 Resent {posted} pending requests.", ephemeral=False)

@bot.tree.command(name="transactions", description="View your grant/withdraw history.")
async def transactions(interaction: Interaction):
    log = load_json(HISTORY_FILE)
    uid = str(interaction.user.id)
    history = log.get(uid, [])
    if not history:
        await interaction.response.send_message("📭 No transaction history found.", ephemeral=False)
        return

    lines = []
    for entry in history[-10:]:
        sign = "+" if entry["amount"] > 0 else ""
        lines.append(f"{sign}{format_currency(entry['amount'], interaction.guild.id)} — {entry['type'].capitalize()} ({entry['reason']})")

    await interaction.response.send_message("📜 Your last 10 transactions:\n" + "\n".join(lines), ephemeral=False)

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
