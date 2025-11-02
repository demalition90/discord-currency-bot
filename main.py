# === TSC Payroll Bot — Dual Balances, Single Channel, Admin Gating, Restore Override ===
# Storage stays in COPPER (ints). Display uses g/s/c emoji via format_currency(...).
# /restore override: EUGENE_ID_OVERRIDE can always run /restore, even without admin role.

import discord
from discord import File
from discord.ext import commands
from discord import app_commands, Interaction
import asyncio
import json
import os
import io
import zipfile
from datetime import datetime

# ---------- CONFIG & CONSTANTS ----------
CONFIG_FILE   = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
HISTORY_FILE  = "transactions.json"

# Hardcoded restore override (update to YOUR Discord user ID if needed)
EUGENE_ID_OVERRIDE = 157650335635079168

# ---------- LOGGING ----------
import logging
logging.basicConfig(level=logging.INFO)

# ---------- INTENTS / BOT ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- UTIL: JSON LOAD/SAVE ----------
def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Corrupt / partially-written file safety
        return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ---------- UTIL: ADMIN / CHANNEL / CURRENCY ----------
def is_admin(interaction: Interaction) -> bool:
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
    admin_roles = set(cfg.get("admin_roles", []))
    if not hasattr(interaction.user, "roles"):
        return False
    return any(role.id in admin_roles for role in interaction.user.roles)

async def enforce_request_channel(interaction: Interaction) -> bool:
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ No config found. Please run `/setup`.", ephemeral=True)
        return False
    req_chan_id = cfg.get("request_channel")
    if not req_chan_id or interaction.channel.id != req_chan_id:
        # Soft error (ephemeral) to guide user to the correct place
        chan = interaction.guild.get_channel(req_chan_id) if req_chan_id else None
        where = chan.mention if chan else "#configured-channel"
        try:
            await interaction.response.send_message(
                f"🚫 Use this command in {where}.",
                ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                f"🚫 Use this command in {where}.",
                ephemeral=True
            )
        return False
    return True

def format_currency(value: int, guild_id: int) -> str:
    cfg = load_json(CONFIG_FILE).get(str(guild_id), {})
    emojis = cfg.get("emojis", {})
    g = emojis.get("gold", "g")
    s = emojis.get("silver", "s")
    c = emojis.get("copper", "c")
    gold  = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{g} {silver:02}{s} {copper:02}{c}"

def ensure_user_bucket(bal):
    """Tolerate legacy (int) -> always return dict with 'banked' and 'debt'."""
    if isinstance(bal, int):
        return {"banked": bal, "debt": 0}
    # Fill missing keys if needed
    return {"banked": int(bal.get("banked", 0)), "debt": int(bal.get("debt", 0))}

# ---------- STARTUP ----------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")
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
                channel = guild.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            else:
                channel = guild.system_channel or discord.utils.get(guild.text_channels, name="general")

            if channel:
                if config_exists and str(guild.id) in config:
                    await channel.send("🔔 Currency bot is online and ready!")
                else:
                    await channel.send(
                        "⚠️ Currency bot has restarted and no configuration was found.\n"
                        "An admin must run `/setup` to configure the bot or `/restore` to load a backup."
                    )
        except Exception as e:
            print(f"⚠️ Could not send startup message in {guild.name}: {e}")

@bot.event
async def on_guild_join(guild):
    try:
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
        )
        if channel:
            await channel.send(
                "👋 Thanks for adding me! Use `/setup` to configure the currency bot.\n"
                "If you're an admin, run `/setup` to choose the command/request channel and admin role.\n"
                "You can use `/restore` if you have a backup ZIP."
            )
    except Exception as e:
        print(f"⚠️ Failed to send join message in {guild.name}: {e}")

# ---------- SETUP ----------
@bot.tree.command(name="setup", description="Configure the bot for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    channel="Single channel used for both commands and request/approval posts",
    role="Admin role for bot actions (approvals, backup, etc.)",
    gold="Gold emoji (e.g. g)",
    silver="Silver emoji (e.g. s)",
    copper="Copper emoji (e.g. c)"
)
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role,
                gold: str = "g", silver: str = "s", copper: str = "c"):
    cfg = load_json(CONFIG_FILE)
    cfg[str(interaction.guild.id)] = {
        "request_channel": channel.id,
        "admin_roles": [role.id],
        "emojis": {"gold": gold, "silver": silver, "copper": copper},
    }
    save_json(CONFIG_FILE, cfg)
    await interaction.response.send_message(
        f"✅ Setup complete!\n"
        f"Commands & requests will use {channel.mention}.\n"
        f"Admin role: `{role.name}`\n"
        f"Emojis: {gold} {silver} {copper}"
    )

# ---------- BACKUP / RESTORE ----------
@bot.tree.command(name="backup", description="Admin: Download all config and data.")
async def backup_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        zip_filename = f"currency_backup_{timestamp}.zip"
        with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in [CONFIG_FILE, BALANCES_FILE, REQUESTS_FILE, HISTORY_FILE]:
                if os.path.exists(file):
                    zipf.write(file)
        await interaction.followup.send("📦 Backup file:", file=File(zip_filename), ephemeral=True)
        os.remove(zip_filename)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to create backup: {e}", ephemeral=True)

@bot.tree.command(name="restore", description="Restore data from a backup ZIP file.")
async def restore(interaction: Interaction, file: discord.Attachment):
    # Override: allow this specific user OR any admin
    if not (is_admin(interaction) or interaction.user.id == EUGENE_ID_OVERRIDE):
        await interaction.response.send_message("❌ You are not authorized to use /restore.", ephemeral=True)
        return
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)

    if not file.filename.lower().endswith(".zip"):
        await interaction.followup.send("🚫 Please upload a valid ZIP file.", ephemeral=True)
        return

    try:
        zip_bytes = await file.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zipf:
            for name in zipf.namelist():
                with zipf.open(name) as src, open(name, "wb") as dst:
                    dst.write(src.read())
        await interaction.followup.send("✅ Restore complete.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Restore failed: {e}", ephemeral=True)

# ---------- COMMANDS: GIVE / TAKE ----------
def normalize_balance_type(balance: str) -> str:
    b = (balance or "").strip().lower()
    if b not in ("banked", "debt"):
        b = "banked"
    return b

@bot.tree.command(name="give", description="(Admin) Grant currency to a user.")
@app_commands.describe(user="Target user", balance="banked or debt", amount="Amount in copper", reason="Reason")
async def give(interaction: Interaction, user: discord.Member, balance: str, amount: int, reason: str):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False, thinking=True)
    balance = normalize_balance_type(balance)
    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    bal = ensure_user_bucket(balances.get(uid, {}))
    bal[balance] = bal.get(balance, 0) + amount
    balances[uid] = bal
    save_json(BALANCES_FILE, balances)

    history = load_json(HISTORY_FILE)
    history.setdefault(uid, []).append(
        {"type": "grant", "amount": amount, "reason": reason, "by": interaction.user.id, "balance": balance}
    )
    save_json(HISTORY_FILE, history)

    await interaction.followup.send(
        f"✅ Granted {format_currency(amount, interaction.guild.id)} ({balance}) to {user.mention}. "
        f"New {balance}: {format_currency(bal[balance], interaction.guild.id)}"
    )

@bot.tree.command(name="take", description="(Admin) Remove currency from a user.")
@app_commands.describe(user="Target user", balance="banked or debt", amount="Amount in copper", reason="Reason")
async def take(interaction: Interaction, user: discord.Member, balance: str, amount: int, reason: str):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False, thinking=True)
    balance = normalize_balance_type(balance)
    balances = load_json(BALANCES_FILE)
    uid = str(user.id)
    bal = ensure_user_bucket(balances.get(uid, {}))
    bal[balance] = max(0, bal.get(balance, 0) - amount)
    balances[uid] = bal
    save_json(BALANCES_FILE, balances)

    history = load_json(HISTORY_FILE)
    history.setdefault(uid, []).append(
        {"type": "deduct", "amount": amount, "reason": reason, "by": interaction.user.id, "balance": balance}
    )
    save_json(HISTORY_FILE, history)

    await interaction.followup.send(
        f"✅ Deducted {format_currency(amount, interaction.guild.id)} ({balance}) from {user.mention}. "
        f"New {balance}: {format_currency(bal[balance], interaction.guild.id)}"
    )

# ---------- COMMANDS: BALANCE / BALANCES ----------
@bot.tree.command(name="balance", description="Check your balance or another user's (admin only).")
@app_commands.describe(user="(Optional) another user to check")
async def balance_command(interaction: Interaction, user: discord.User = None):
    if not await enforce_request_channel(interaction):
        return

    target = user or interaction.user
    is_self = (target.id == interaction.user.id)
    if not is_self and not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized to view other users' balances.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    balances = load_json(BALANCES_FILE)
    bal = ensure_user_bucket(balances.get(str(target.id), {}))
    banked_str = format_currency(bal["banked"], interaction.guild.id)
    debt_str   = format_currency(bal["debt"],   interaction.guild.id)
    await interaction.followup.send(
        f"💰 Balances for {'you' if is_self else target.mention}: {banked_str} banked, {debt_str} debt",
        ephemeral=True
    )

@bot.tree.command(name="balances", description="Admin only: show all users’ balances.")
async def balances_command(interaction: Interaction):
    """List balances for all users sorted by total (banked + debt), emoji-formatted."""
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized to view all balances.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    if not balances:
        await interaction.response.send_message("📊 No balances found.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    total_banked = 0
    total_debt = 0
    msg_lines = ["**📊 All User Balances:**"]

    def combined_total(item):
        _uid, b = item
        if isinstance(b, int):
            return b
        return int(b.get("banked", 0)) + int(b.get("debt", 0))

    sorted_entries = sorted(balances.items(), key=combined_total, reverse=True)

    for user_id, b in sorted_entries:
        b = ensure_user_bucket(b)
        total_banked += b["banked"]
        total_debt   += b["debt"]

        # Resolve display name
        try:
            u = await interaction.client.fetch_user(int(user_id))
            name = u.name
        except Exception:
            name = f"User {user_id}"

        banked_str = format_currency(b["banked"], interaction.guild.id)
        debt_str   = format_currency(b["debt"],   interaction.guild.id)
        msg_lines.append(f"{name}: {banked_str} banked, {debt_str} debt")

    msg_lines.append("")
    total_banked_str = format_currency(total_banked, interaction.guild.id)
    total_debt_str   = format_currency(total_debt,   interaction.guild.id)
    msg_lines.append(f"**Total:** {total_banked_str} banked, {total_debt_str} debt")

    await interaction.followup.send("\n".join(msg_lines), allowed_mentions=discord.AllowedMentions.none(), ephemeral=True)

# ---------- COMMANDS: REQUEST / TRANSFER ----------
@bot.tree.command(name="request", description="Request currency (queued for admin approval).")
@app_commands.describe(balance="banked or debt", amount="Amount in copper", reason="Reason")
async def request_command(interaction: Interaction, balance: str, amount: int, reason: str):
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.defer(ephemeral=False, thinking=True)

    balance = normalize_balance_type(balance)
    reqs = load_json(REQUESTS_FILE)
    req_id = str(interaction.id)
    reqs[req_id] = {
        "type": "request",
        "user_id": str(interaction.user.id),
        "amount": int(amount),
        "reason": reason,
        "balance": balance
    }
    save_json(REQUESTS_FILE, reqs)

    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
    channel = interaction.guild.get_channel(cfg.get("request_channel"))
    if not channel:
        await interaction.followup.send("❌ Request channel not configured.", ephemeral=False)
        return

    embed = discord.Embed(
        title="Currency Request",
        description=f"{interaction.user.mention} is requesting {format_currency(amount, interaction.guild.id)} ({balance})\nReason: {reason}",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Request | User: {interaction.user.id} | Amount: {amount} | Balance: {balance}")
    try:
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await interaction.followup.send("📝 Your request has been submitted for approval.", ephemeral=False)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to post in the configured channel.", ephemeral=True)

@bot.tree.command(name="transfer", description="Request a currency transfer between users (admin approved).")
@app_commands.describe(balance="banked or debt", from_user="Sender", to_user="Recipient", amount="Amount in copper", reason="Reason")
async def transfer_command(interaction: Interaction, balance: str, from_user: discord.User, to_user: discord.User, amount: int, reason: str):
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.defer(ephemeral=False, thinking=True)

    balance = normalize_balance_type(balance)
    is_admin_user = is_admin(interaction)
    if not is_admin_user and from_user.id != interaction.user.id:
        await interaction.followup.send("❌ You can only request transfers from your own account.", ephemeral=True)
        return

    reqs = load_json(REQUESTS_FILE)
    req_id = str(interaction.id)
    reqs[req_id] = {
        "type": "transfer",
        "from": str(from_user.id),
        "to": str(to_user.id),
        "amount": int(amount),
        "reason": reason,
        "balance": balance
    }
    save_json(REQUESTS_FILE, reqs)

    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
    channel = interaction.guild.get_channel(cfg.get("request_channel"))
    if not channel:
        await interaction.followup.send("❌ Request channel not configured.", ephemeral=True)
        return

    amount_str = format_currency(amount, interaction.guild.id)
    embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
    embed.add_field(name="From", value=from_user.mention, inline=True)
    embed.add_field(name="To", value=to_user.mention, inline=True)
    embed.add_field(name="Amount", value=f"{amount_str} ({balance})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Transfer | From: {from_user.id} | To: {to_user.id} | Amount: {amount} | Balance: {balance}")

    try:
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await interaction.followup.send("📨 Transfer request submitted for approval.", ephemeral=False)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to post in the configured channel.", ephemeral=True)

# ---------- COMMANDS: TRANSACTIONS / SETTINGS / HELP / REFRESH / RESCAN ----------
@bot.tree.command(name="transactions", description="View recent transactions.")
@app_commands.describe(user="(Admin) Another user to view")
async def transactions_command(interaction: Interaction, user: discord.User = None):
    if not await enforce_request_channel(interaction):
        return
    is_self = (not user) or (user.id == interaction.user.id)
    if not is_self and not is_admin(interaction):
        await interaction.response.send_message("❌ You don't have permission to view other users' transactions.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    user_id = str(user.id if user else interaction.user.id)
    history = load_json(HISTORY_FILE).get(user_id, [])
    if not history:
        await interaction.followup.send("📜 No transaction history found.", ephemeral=True)
        return

    lines = ["**📜 Last 10 transactions:**"]
    # Show most recent last (chronological display)
    for entry in list(history)[-10:]:
        if isinstance(entry, str):
            lines.append(entry)
            continue
        t = entry.get("type", "")
        amt = int(entry.get("amount", 0))
        bal = entry.get("balance", "banked")
        sign = "+" if t in ("grant", "request", "transfer_in") else "-"
        lines.append(f"{sign}{format_currency(amt, interaction.guild.id)} — {t.replace('_',' ').title()} ({bal}) — {entry.get('reason','')}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)

@bot.tree.command(name="settings", description="Show the current bot config for this server.")
async def settings_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ No config found. Please run `/setup`.", ephemeral=True)
        return
    chan = interaction.guild.get_channel(cfg["request_channel"])
    roles = [interaction.guild.get_role(rid) for rid in cfg.get("admin_roles", [])]
    emoji = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
    msg = (
        f"📥 Channel: {chan.mention if chan else 'Unknown'}\n"
        f"🔑 Admin Roles: {', '.join(r.name for r in roles if r)}\n"
        f"💰 Emojis: {emoji['gold']} {emoji['silver']} {emoji['copper']}"
    )
    await interaction.response.send_message(msg, ephemeral=False)

@bot.tree.command(name="help", description="Show usage and commands.")
async def help_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.send_message(
        "🧾 **Currency Bot Commands**\n"
        "- `/balance` — Check your balance\n"
        "- `/balances` — (Admin) All balances (emoji formatted)\n"
        "- `/request` — Request currency (admin approval)\n"
        "- `/transfer` — Request to send currency to another user (admin approval)\n"
        "- `/transactions` — View your last 10 transactions\n"
        "- `/setup` — (Admin) Configure channel, admin role, emojis\n"
        "- `/give` & `/take` — (Admin) Grant/remove currency (banked/debt)\n"
        "- `/backup` — (Admin) Download config/data\n"
        "- `/restore` — Restore from ZIP (admins + your override)\n"
        "- `/rescan_requests` — (Admin) Repost any unprocessed requests\n",
        ephemeral=False
    )

@bot.tree.command(name="refresh", description="(Admin) Re-sync slash commands.")
async def refresh_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        synced = await bot.tree.sync()
        await interaction.followup.send(f"🔁 Synced {len(synced)} commands.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Sync failed: {e}", ephemeral=True)

@bot.tree.command(name="rescan_requests", description="(Admin) Repost any unprocessed requests.")
async def rescan_requests(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not cfg:
        await interaction.followup.send("❌ Bot not configured. Run `/setup`.", ephemeral=True)
        return

    reqs = load_json(REQUESTS_FILE)
    if not reqs:
        await interaction.followup.send("📭 No pending requests found.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(cfg.get("request_channel"))
    if not channel:
        await interaction.followup.send("❌ Could not fetch configured channel.", ephemeral=True)
        return

    reposted = 0
    for key, data in list(reqs.items()):
        try:
            t = data.get("type")
            if t == "request":
                user = await interaction.client.fetch_user(int(data["user_id"]))
                amount_str = format_currency(int(data["amount"]), interaction.guild.id)
                balance = data.get("balance", "banked")
                embed = discord.Embed(
                    title="Currency Request",
                    description=f"{user.mention} is requesting {amount_str} ({balance})\nReason: {data.get('reason','')}",
                    color=discord.Color.gold()
                )
                embed.set_footer(text=f"Request | User: {data['user_id']} | Amount: {data['amount']} | Balance: {balance}")
            elif t == "transfer":
                from_user = await interaction.client.fetch_user(int(data["from"]))
                to_user   = await interaction.client.fetch_user(int(data["to"]))
                amount_str = format_currency(int(data["amount"]), interaction.guild.id)
                balance = data.get("balance", "banked")
                embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
                embed.add_field(name="From", value=from_user.mention, inline=True)
                embed.add_field(name="To", value=to_user.mention, inline=True)
                embed.add_field(name="Amount", value=f"{amount_str} ({balance})", inline=False)
                embed.add_field(name="Reason", value=data.get("reason",""), inline=False)
                embed.set_footer(text=f"Transfer | From: {data['from']} | To: {data['to']} | Amount: {data['amount']} | Balance: {balance}")
            else:
                continue

            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            reposted += 1
        except Exception as e:
            print(f"[rescan_requests] Failed to repost: {e}")
            continue

    await interaction.followup.send(f"🔄 Reposted {reposted} request(s).", ephemeral=True)

# ---------- REACTION APPROVALS ----------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # Ignore own reactions
    if payload.user_id == bot.user.id:
        return
    # Only process checkmark / cross
    if str(payload.emoji) not in ("✅", "❌"):
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    cfg = load_json(CONFIG_FILE).get(str(guild.id), {})
    req_channel_id = cfg.get("request_channel")
    if not req_channel_id or payload.channel_id != req_channel_id:
        return  # Only in configured channel

    channel = guild.get_channel(payload.channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    if not message.embeds:
        return

    # Admin-only approvals
    member = guild.get_member(payload.user_id)
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
    admin_roles = set(cfg.get("admin_roles", []))
    if not any(r.id in admin_roles for r in getattr(member, "roles", [])):
        return

    embed = message.embeds[0]
    footer = embed.footer.text or ""
    if not footer:
        return

    reqs = load_json(REQUESTS_FILE)
    balances = load_json(BALANCES_FILE)
    history = load_json(HISTORY_FILE)

    def fmt(val: int) -> str:
        return format_currency(val, guild.id)

    approved = (str(payload.emoji) == "✅")

    # Parse footer variants:
    # "Request | User: <uid> | Amount: <amt> | Balance: <banked|debt>"
    # "Transfer | From: <uid> | To: <uid> | Amount: <amt> | Balance: <banked|debt>"
    try:
        if footer.startswith("Request"):
            uid = footer.split("User: ")[1].split(" |")[0]
            amount = int(footer.split("Amount: ")[1].split(" |")[0])
            balance = footer.split("Balance: ")[1].split(" |")[0] if "Balance:" in footer else "banked"

            for key, data in list(reqs.items()):
                if data.get("type") == "request" and data.get("user_id") == uid and int(data.get("amount",0)) == amount:
                    bal = ensure_user_bucket(balances.get(uid, {}))
                    if approved:
                        bal[balance] = bal.get(balance, 0) + amount
                        balances[uid] = bal
                        history.setdefault(uid, []).append(
                            {"type": "request", "amount": amount, "reason": data.get("reason",""), "by": "approval", "balance": balance}
                        )
                        await channel.send(
                            f"✅ Approved {fmt(amount)} ({balance}) to <@{uid}>. "
                            f"New {balance}: {fmt(bal[balance])}"
                        )
                    else:
                        await channel.send(f"❌ Denied request by <@{uid}>.")
                    del reqs[key]
                    break

        elif footer.startswith("Transfer"):
            from_id = footer.split("From: ")[1].split(" |")[0]
            to_id   = footer.split("To: ")[1].split(" |")[0]
            amount  = int(footer.split("Amount: ")[1].split(" |")[0])
            balance = footer.split("Balance: ")[1].split(" |")[0] if "Balance:" in footer else "banked"

            for key, data in list(reqs.items()):
                if (data.get("type") == "transfer" and data.get("from") == from_id and
                    data.get("to") == to_id and int(data.get("amount",0)) == amount):
                    from_bal = ensure_user_bucket(balances.get(from_id, {}))
                    to_bal   = ensure_user_bucket(balances.get(to_id, {}))
                    if approved:
                        if from_bal.get(balance, 0) >= amount:
                            from_bal[balance] -= amount
                            to_bal[balance]    = to_bal.get(balance, 0) + amount
                            balances[from_id]  = from_bal
                            balances[to_id]    = to_bal
                            history.setdefault(from_id, []).append(
                                {"type": "transfer_out", "amount": amount, "reason": data.get("reason",""), "by": to_id, "balance": balance}
                            )
                            history.setdefault(to_id, []).append(
                                {"type": "transfer_in", "amount": amount, "reason": data.get("reason",""), "by": from_id, "balance": balance}
                            )
                            await channel.send(
                                f"✅ Transfer approved! <@{from_id}> ➜ <@{to_id}> {fmt(amount)} ({balance})"
                            )
                        else:
                            await channel.send(
                                f"❌ Transfer failed: <@{from_id}> doesn't have enough {balance}."
                            )
                    else:
                        await channel.send(f"❌ Transfer denied for <@{from_id}>.")
                    del reqs[key]
                    break
    except Exception as e:
        print(f"[on_raw_reaction_add] error: {e}")

    save_json(REQUESTS_FILE, reqs)
    save_json(BALANCES_FILE, balances)
    save_json(HISTORY_FILE, history)

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
