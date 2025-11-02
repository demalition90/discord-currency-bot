"""
Currency Bot with dual balances (banked and debt).

This bot manages a simple virtual currency within a Discord server. Users can
request funds, transfer currency to other members, and view their balances.
Admins can grant or deduct currency. Two balances are maintained per user: a
"banked" balance representing positive funds and a "debt" balance representing
what the user owes. All commands that modify a balance require the caller to
specify which balance type ("banked" or "debt") is affected.

Key features:
* Slash commands for setup, balance checks, currency requests, transfers,
  grants and deductions.
* Admin‑only approvals of requests via reaction emojis.
* Restriction of all commands to a designated channel (defined during setup).
* Dual balances per user (banked and debt) with combined sorting in
  `/balances` and a grand total line.
* Responses are deferred to avoid Discord's 3‑second timeout.
* Persistent JSON files for configuration, balances, pending requests and
  transaction history.
"""

import discord
import io
import json
import os
import zipfile
from datetime import datetime
from discord import File
from discord.ext import commands
from discord import app_commands, Interaction
import logging


# Configure logging
logging.basicConfig(level=logging.INFO)

# File paths
CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"
HISTORY_FILE = "transactions.json"


def load_json(path: str):
    """Load a JSON file, returning an empty dict if the file does not exist."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_json(path: str, data) -> None:
    """Write a Python object to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def format_currency(value: int, guild_id: int) -> str:
    """Format an integer currency value into a human‑readable string.

    Values are in copper; the string is formatted as `<gold>g <silver>s <copper>c`
    using custom emoji codes configured per guild.
    """
    config = load_json(CONFIG_FILE)
    emojis = config.get(str(guild_id), {}).get("emojis", {})
    gold_emoji = emojis.get("gold", "g")
    silver_emoji = emojis.get("silver", "s")
    copper_emoji = emojis.get("copper", "c")
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{gold_emoji} {silver:02}{silver_emoji} {copper:02}{copper_emoji}"


def is_admin(interaction: Interaction) -> bool:
    """Return True if the invoking user has one of the configured admin roles."""
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(interaction.guild.id), {})
    allowed_roles = guild_cfg.get("admin_roles", [])
    return any(role.id in allowed_roles for role in interaction.user.roles)


async def enforce_request_channel(interaction: Interaction) -> bool:
    """
    Ensure a command runs in the configured request channel.

    If a request channel is set for the guild and the invocation happens in a
    different channel, an error is sent and False is returned. Otherwise
    returns True to allow execution to proceed.
    """
    try:
        cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
        req_channel_id = cfg.get("request_channel")
        if req_channel_id and interaction.channel.id != req_channel_id:
            chan = interaction.guild.get_channel(req_channel_id)
            mention = chan.mention if chan else "the designated channel"
            await interaction.response.send_message(
                f"🚫 This command can only be used in {mention}.",
                ephemeral=True
            )
            return False
        return True
    except Exception:
        # If something goes wrong, allow execution rather than silently failing
        return True


def ensure_user_balances(user_id: str):
    """Ensure that a user has both banked and debt entries in balances.json."""
    balances = load_json(BALANCES_FILE)
    if user_id not in balances:
        balances[user_id] = {"banked": 0, "debt": 0}
        save_json(BALANCES_FILE, balances)
        return balances[user_id]
    entry = balances[user_id]
    # If the entry is an int from older versions, convert to dict
    if isinstance(entry, int):
        balances[user_id] = {"banked": entry, "debt": 0}
        save_json(BALANCES_FILE, balances)
        return balances[user_id]
    # Otherwise ensure keys exist
    if "banked" not in entry:
        entry["banked"] = 0
    if "debt" not in entry:
        entry["debt"] = 0
    save_json(BALANCES_FILE, balances)
    return entry


# Initialise the bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed to fetch member roles in reaction handler
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"⚠️ Sync failed: {e}")
    # Send startup message to each guild
    config = load_json(CONFIG_FILE)
    for guild in bot.guilds:
        try:
            cfg = config.get(str(guild.id), {})
            chan_id = cfg.get("request_channel")
            channel = None
            if chan_id:
                channel = guild.get_channel(chan_id) or await bot.fetch_channel(chan_id)
            else:
                channel = guild.system_channel or discord.utils.get(guild.text_channels, name="general")
            if channel:
                if cfg:
                    await channel.send("🔔 Currency bot is now online and ready!")
                else:
                    await channel.send(
                        "⚠️ Currency bot has restarted and no configuration was found.\n"
                        "An admin must run `/setup` to reconfigure the bot or `/restore` to restore from backup."
                    )
        except Exception as e:
            print(f"⚠️ Could not send startup message in {guild.name}: {e}")


@bot.event
async def on_guild_join(guild):
    print(f"➕ Joined new guild: {guild.name} ({guild.id})")
    # Send a greeting message in the first available channel
    try:
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None
        )
        if channel:
            await channel.send(
                "👋 Thanks for adding me! Use `/setup` to configure the currency bot.\n"
                "Admins should run `/setup` to define which role can approve requests and which channel to use,"
                " or `/restore` if you have a backup file."
            )
    except Exception as e:
        print(f"⚠️ Failed to send join message in {guild.name}: {e}")


@bot.tree.command(name="setup", description="Configure the bot for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    channel="Channel where all bot commands and request posts should appear",
    role="Role considered admin for bot commands",
    gold="Gold emoji (optional)",
    silver="Silver emoji (optional)",
    copper="Copper emoji (optional)"
)
async def setup(interaction: Interaction, channel: discord.TextChannel, role: discord.Role,
                gold: str = "g", silver: str = "s", copper: str = "c"):
    """Set the command/request channel, admin role and optional currency emojis."""
    # Persist configuration for this guild. The selected channel will be used both for posting
    # requests/transfers and for accepting slash commands (the bot will only respond in this channel).
    config = load_json(CONFIG_FILE)
    config[str(interaction.guild.id)] = {
        "request_channel": channel.id,
        "admin_roles": [role.id],
        "emojis": {"gold": gold, "silver": silver, "copper": copper},
    }
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Setup complete!\nAll commands and requests will use {channel.mention}.\nAdmin role: `{role.name}`\n"
        f"Emojis: {gold} {silver} {copper}",
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="backup", description="Download all config and data files (admin only).")
@app_commands.check(lambda i: is_admin(i))
async def backup_command(interaction: Interaction):
    """Allow an administrator to download a ZIP backup of all data files."""
                                    
    if not await enforce_request_channel(interaction):
        return
                                         
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        zip_name = f"currency_backup_{timestamp}.zip"
                                                                                          
        with zipfile.ZipFile(zip_name, 'w') as zipf:
            for filename in [CONFIG_FILE, BALANCES_FILE, REQUESTS_FILE, HISTORY_FILE]:
                if os.path.exists(filename):
                    zipf.write(filename)
        backup_file = File(zip_name)
                                        
        await interaction.followup.send("📦 Backup file:", file=backup_file, ephemeral=True)
        os.remove(zip_name)
    except Exception as e:
                                                         
        await interaction.followup.send(f"❌ Failed to create backup: {e}",
                                       ephemeral=True)


                  
                                                                               
                                                          
                                                              
                                                       
                                        


@bot.tree.command(name="restore", description="Restore data from a backup ZIP file.")
@app_commands.check(lambda i: is_admin(i))
async def restore(interaction: Interaction, file: discord.Attachment):
    """Allow an administrator to restore all data files from an uploaded backup ZIP archive."""
                                    
    if not await enforce_request_channel(interaction):
        return
                                           
                                                                                        
                                                                                            
    await interaction.response.defer(ephemeral=True, thinking=True)
                             
    if not file.filename.lower().endswith(".zip"):
        await interaction.followup.send("🚫 Please upload a valid ZIP file.",
                                       ephemeral=True)
        return
    try:
        zip_bytes = await file.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zipf:
            for name in zipf.namelist():
                with zipf.open(name) as zf:
                    with open(name, 'wb') as out:
                        out.write(zf.read())
        await interaction.followup.send("✅ Restore complete.",
                                       ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Restore failed: {e}",
                                       ephemeral=True)


@bot.tree.command(name="give", description="(Admin) Grant currency to a user.")
@app_commands.describe(user="Recipient", balance="Balance type (banked or debt)", amount="Amount in copper", reason="Reason for grant")
@app_commands.choices(balance=[
    app_commands.Choice(name="banked", value="banked"),
    app_commands.Choice(name="debt", value="debt")
])
async def give(interaction: Interaction, user: discord.Member, balance: str, amount: int, reason: str):
    """Grant currency to a user. Specify which balance (banked or debt) to increase."""
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.",
                                              ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        balances = load_json(BALANCES_FILE)
        uid = str(user.id)
                                    
        user_bal = ensure_user_balances(uid)
                                   
        user_bal[balance] = user_bal.get(balance, 0) + amount
        balances[uid] = user_bal
        save_json(BALANCES_FILE, balances)
                         
        history = load_json(HISTORY_FILE)
        entry = {
            "type": "grant",
            "balance": balance,
            "amount": amount,
            "reason": reason,
            "by": interaction.user.id,
        }
        history.setdefault(uid, []).append(entry)
        save_json(HISTORY_FILE, history)
                
        new_bal = user_bal[balance]
        await interaction.followup.send(
            f"✅ Granted {format_currency(amount, interaction.guild.id)} to {user.mention} ({balance}). "
            f"New {balance} balance: {format_currency(new_bal, interaction.guild.id)}",
            allowed_mentions=discord.AllowedMentions.none()
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error during /give: {e}",
                                       allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="take", description="(Admin) Remove currency from a user.")
@app_commands.describe(user="Target user", balance="Balance type (banked or debt)", amount="Amount in copper", reason="Reason for deduction")
@app_commands.choices(balance=[
    app_commands.Choice(name="banked", value="banked"),
    app_commands.Choice(name="debt", value="debt")
])
async def take(interaction: Interaction, user: discord.User, balance: str, amount: int, reason: str):
    """Deduct currency from a user's specified balance (banked or debt)."""
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.",
                                              ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        balances = load_json(BALANCES_FILE)
        uid = str(user.id)
        user_bal = ensure_user_balances(uid)
                                              
        current = user_bal.get(balance, 0)
        user_bal[balance] = max(0, current - amount)
        balances[uid] = user_bal
        save_json(BALANCES_FILE, balances)
                         
        history = load_json(HISTORY_FILE)
        entry = {
            "type": "deduct",
            "balance": balance,
            "amount": amount,
            "reason": reason,
            "by": interaction.user.id,
        }
        history.setdefault(uid, []).append(entry)
        save_json(HISTORY_FILE, history)
        await interaction.followup.send(
            f"✅ Deducted {format_currency(amount, interaction.guild.id)} from {user.mention} ({balance}). "
            f"New {balance} balance: {format_currency(user_bal[balance], interaction.guild.id)}",
            allowed_mentions=discord.AllowedMentions.none()
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error during /take: {e}",
                                       allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="balance", description="Check your balance or another user's (admin only).")
@app_commands.describe(user="(Optional) Another user to check the balances of")
async def balance_command(interaction: Interaction, user: discord.User = None):
    """Display a user's banked and debt balances."""
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
        if not cfg:
            await interaction.followup.send("❌ No config found. Please run `/setup`.",
                                           ephemeral=True)
            return
        target = user or interaction.user
                                                
        if target.id != interaction.user.id and not is_admin(interaction):
            await interaction.followup.send("❌ You are not authorized to view other users' balances.",
                                           ephemeral=True)
            return
        uid = str(target.id)
        user_bal = ensure_user_balances(uid)
        banked_str = format_currency(user_bal.get("banked", 0), interaction.guild.id)
        debt_str = format_currency(user_bal.get("debt", 0), interaction.guild.id)
        await interaction.followup.send(
            f"💰 Balances for {'you' if target.id == interaction.user.id else target.mention}: "
            f"Banked: {banked_str} • Debt: {debt_str}",
            allowed_mentions=discord.AllowedMentions.none()
        )
    except Exception as e:
        print(f"[ERROR] /balance failed: {e}")
        await interaction.followup.send("❌ An error occurred while processing your request.",
                                       ephemeral=True)


@bot.tree.command(name="balances", description="Admin only: show all users’ balances.")
async def balances_command(interaction: Interaction):
    """List balances for all users sorted by total (banked + debt) and show emoji-formatted totals."""
    # Gate: must be in the configured command/request channel
    if not await enforce_request_channel(interaction):
        return
    # Gate: must be an admin (as defined in /setup)
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized to view all balances.", ephemeral=True)
        return

    balances = load_json(BALANCES_FILE)
    if not balances:
        await interaction.response.send_message("📊 No balances found.", ephemeral=True)
        return

    # Defer reply to avoid Discord 3s timeout
    await interaction.response.defer(ephemeral=True, thinking=True)

    total_banked = 0
    total_debt = 0
    msg_lines = ["**📊 All User Balances:**"]

    # Sort by combined total (banked + debt) descending
    def combined_total(item):
        _uid, bal = item
        if isinstance(bal, int):
            # legacy one-bucket storage
            return bal
        return bal.get("banked", 0) + bal.get("debt", 0)

    sorted_entries = sorted(balances.items(), key=combined_total, reverse=True)

    for user_id, bal in sorted_entries:
        # Upgrade legacy int -> dict on the fly for safety
        if isinstance(bal, int):
            bal = {"banked": bal, "debt": 0}

        banked = bal.get("banked", 0)
        debt = bal.get("debt", 0)
        total_banked += banked
        total_debt += debt

        # Resolve username (fallback to raw mention if lookup fails)
        try:
            user_obj = await interaction.client.fetch_user(int(user_id))
            name = user_obj.name
        except Exception:
            name = f"User {user_id}"

        # 👇 Emoji-formatted outputs
        banked_str = format_currency(banked, interaction.guild.id)
        debt_str   = format_currency(debt,   interaction.guild.id)

        msg_lines.append(f"{name}: {banked_str} banked, {debt_str} debt")

    # Grand totals (emoji-formatted)
    msg_lines.append("")
    total_banked_str = format_currency(total_banked, interaction.guild.id)
    total_debt_str   = format_currency(total_debt,   interaction.guild.id)
    msg_lines.append(f"**Total:** {total_banked_str} banked, {total_debt_str} debt")

    await interaction.followup.send(
        "\n".join(msg_lines),
        allowed_mentions=discord.AllowedMentions.none(),
        ephemeral=True
    )



@bot.tree.command(name="request", description="Request currency from the server.")
@app_commands.describe(balance="Balance type (banked or debt)", amount="Amount in copper", reason="Reason for request")
@app_commands.choices(balance=[
    app_commands.Choice(name="banked", value="banked"),
    app_commands.Choice(name="debt", value="debt")
])
async def request_command(interaction: Interaction, balance: str, amount: int, reason: str):
    """Submit a currency request to be approved by an admin."""
                            
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id), {})
    req_channel_id = cfg.get("request_channel")
    if req_channel_id and interaction.channel.id != req_channel_id:
        chan = interaction.guild.get_channel(req_channel_id)
        await interaction.response.send_message(
            f"🚫 Please use this command in {chan.mention if chan else 'the configured request channel' }.",
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
                           
        reqs = load_json(REQUESTS_FILE)
        req_id = str(interaction.id)
        reqs[req_id] = {
            "type": "request",
            "user_id": str(interaction.user.id),
            "balance": balance,
            "amount": amount,
            "reason": reason,
        }
        save_json(REQUESTS_FILE, reqs)
                                       
        channel = interaction.guild.get_channel(req_channel_id)
        if not channel:
            channel = await interaction.guild.fetch_channel(req_channel_id)
        embed = discord.Embed(
            title="Currency Request",
            description=(
                f"{interaction.user.mention} is requesting {format_currency(amount, interaction.guild.id)}\n"
                f"Balance: {balance.capitalize()}\n"
                f"Reason: {reason}"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Request | User: {interaction.user.id} | Balance: {balance} | Amount: {amount}")
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
        except discord.Forbidden:
                                                                 
            await interaction.followup.send(
                "❌ Failed to submit request: the bot does not have permission to send messages in the request channel.",
                allowed_mentions=discord.AllowedMentions.none()
            )
            return
        except Exception as send_err:
            await interaction.followup.send(
                f"❌ Failed to submit request: {send_err}",
                allowed_mentions=discord.AllowedMentions.none()
            )
            return
                                    
        await interaction.followup.send(
            "📝 Your request has been submitted for approval.",
            allowed_mentions=discord.AllowedMentions.none()
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to submit request: {e}",
            allowed_mentions=discord.AllowedMentions.none()
        )


@bot.tree.command(name="transfer", description="Request a currency transfer from one user to another.")
@app_commands.describe(
    from_user="User sending the currency",
    to_user="User receiving the currency",
    balance="Balance type (banked or debt)",
    amount="Amount in copper",
    reason="Reason for transfer"
)
@app_commands.choices(balance=[
    app_commands.Choice(name="banked", value="banked"),
    app_commands.Choice(name="debt", value="debt")
])
async def transfer_command(
    interaction: Interaction,
    from_user: discord.User,
    to_user: discord.User,
    balance: str,
    amount: int,
    reason: str
):
    """Request a transfer of currency between two users. Admin required if transferring from another user."""
    config = load_json(CONFIG_FILE)
    cfg = config.get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message("❌ No configuration found. Please run `/setup`.",
                                              ephemeral=True)
        return
    req_channel_id = cfg.get("request_channel")
    if not req_channel_id:
        await interaction.response.send_message("🚫 No request channel configured. Admin must run `/setup`.",
                                              ephemeral=True)
        return
    if interaction.channel.id != req_channel_id:
        chan = interaction.guild.get_channel(req_channel_id)
        await interaction.response.send_message(
            f"🚫 Please use this command in {chan.mention if chan else 'the configured request channel' }.",
            ephemeral=True
        )
        return
    # Only allow non‑admins to transfer from themselves
    if not is_admin(interaction) and from_user.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ You can only request transfers from your own account.",
            ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        reqs = load_json(REQUESTS_FILE)
        req_id = str(interaction.id)
        reqs[req_id] = {
            "type": "transfer",
            "from": str(from_user.id),
            "to": str(to_user.id),
            "balance": balance,
            "amount": amount,
            "reason": reason,
        }
        save_json(REQUESTS_FILE, reqs)
                     
        amount_str = format_currency(amount, interaction.guild.id)
        embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
        embed.add_field(name="From", value=from_user.mention, inline=True)
        embed.add_field(name="To", value=to_user.mention, inline=True)
        embed.add_field(name="Amount", value=amount_str, inline=False)
        embed.add_field(name="Balance", value=balance.capitalize(), inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Transfer | From: {from_user.id} | To: {to_user.id} | Balance: {balance} | Amount: {amount}")
        channel = interaction.guild.get_channel(req_channel_id) or await interaction.guild.fetch_channel(req_channel_id)
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Failed to submit transfer: the bot does not have permission to send messages in the request channel.",
                allowed_mentions=discord.AllowedMentions.none()
            )
            return
        except Exception as send_err:
            await interaction.followup.send(
                f"❌ Failed to submit transfer: {send_err}",
                allowed_mentions=discord.AllowedMentions.none()
            )
            return
        await interaction.followup.send(
            "📨 Transfer request submitted for approval.",
            allowed_mentions=discord.AllowedMentions.none()
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to submit transfer: {e}",
            allowed_mentions=discord.AllowedMentions.none()
        )


@bot.tree.command(name="transactions", description="View your recent transactions.")
@app_commands.describe(user="User to view (admin only)")
async def transactions_command(interaction: Interaction, user: discord.User = None):
    """Display the 10 most recent transactions for yourself or another user."""
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
                                                       
        if user and not is_admin(interaction):
            await interaction.followup.send(
                "❌ You don't have permission to view other users' transactions.",
                ephemeral=True
            )
            return
        target_id = str(user.id if user else interaction.user.id)
        history = load_json(HISTORY_FILE)
        user_history = history.get(target_id, [])
        if not user_history:
            await interaction.followup.send(
                "📜 No transaction history found.",
                ephemeral=True
            )
            return
        msg = "**📜 Your last 10 transactions:**\n"
        for entry in reversed(user_history[-10:]):
            if isinstance(entry, dict):
                tx_type = entry.get("type", "").replace("_", " ").capitalize()
                bal_type = entry.get("balance", "")
                # Determine sign: positive for grants/requests/transfer in, negative otherwise
                if tx_type.lower() in ("grant", "request", "transfer in"):
                    sign = "+"
                else:
                    sign = "-"
                amount_val = entry.get("amount", 0)
                amount_str = format_currency(abs(amount_val), interaction.guild.id)
                reason = entry.get("reason", "")
                bal_label = f"{bal_type.capitalize()}"
                msg += f"{sign}{amount_str} — {tx_type} ({bal_label}) ({reason})\n"
            else:
                # Legacy string entry
                msg += f"{entry}\n"
        await interaction.followup.send(msg,
                                       ephemeral=True)
    except Exception as e:
        print(f"[ERROR] /transactions failed: {e}")
        await interaction.followup.send(
            "❌ An internal error occurred while processing your request.",
            ephemeral=True
        )


@bot.tree.command(name="settings", description="Show the current bot config for this server.")
async def settings_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message(
            "❌ No config found. Please run /setup.",
            ephemeral=False
        )
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        chan = interaction.guild.get_channel(cfg["request_channel"])
        roles = [interaction.guild.get_role(rid) for rid in cfg.get("admin_roles", [])]
        emoji = cfg.get("emojis", {"gold": "g", "silver": "s", "copper": "c"})
        msg = (
            f"📥 Request Channel: {chan.mention if chan else 'Unknown'}\n"
            f"🔑 Admin Roles: {', '.join(r.name for r in roles if r)}\n"
            f"💰 Emojis: {emoji['gold']} {emoji['silver']} {emoji['copper']}"
        )
        await interaction.followup.send(msg,
                                       ephemeral=False)
    except Exception as e:
        await interaction.followup.send(
            f"⚠️ Failed to show settings: {e}",
            ephemeral=False
        )


@bot.tree.command(name="help", description="Show usage and commands.")
async def help_command(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    await interaction.response.send_message(
        """🧾 **Currency Bot Commands**
- `/balance` — Check your balance
- `/request` — Request currency (admins approve)
- `/transfer` — Request to send currency to another user
- `/transactions` — View your history
- `/setup` — (Admin) Configure the bot
- `/give` and `/take` — (Admin) Grant or remove currency
- `/backup` and `/restore` — (Admin) Backup or restore data
- `/rescan_requests` — (Admin) Repost missed requests
- `/settings` — View config info
""",
    )


@bot.tree.command(name="refresh", description="Admin: Force re-sync of slash commands.")
async def refresh(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ You are not authorized.",
            ephemeral=False
        )
        return
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        synced = await bot.tree.sync()
        await interaction.followup.send(
            f"🔁 Synced {len(synced)} commands.",
            ephemeral=False
        )
    except Exception as e:
        await interaction.followup.send(
            f"⚠️ Sync failed: {e}",
            ephemeral=False
        )


@bot.tree.command(name="rescan_requests", description="Admin: Repost any unprocessed requests (e.g., after a restart).")
async def rescan_requests(interaction: Interaction):
    if not await enforce_request_channel(interaction):
        return
    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ You are not authorized.",
            ephemeral=True
        )
        return
    cfg = load_json(CONFIG_FILE).get(str(interaction.guild.id))
    if not cfg:
        await interaction.response.send_message(
            "❌ Bot is not configured. Please run `/setup`.",
            ephemeral=True
        )
        return
    req_channel_id = cfg.get("request_channel")
    reqs = load_json(REQUESTS_FILE)
    if not reqs:
        await interaction.response.send_message(
            "📭 No pending requests found.",
            ephemeral=True
        )
        return
    channel = interaction.guild.get_channel(req_channel_id)
    if not channel:
        try:
            channel = await interaction.guild.fetch_channel(req_channel_id)
        except Exception:
            await interaction.response.send_message(
                "❌ Could not fetch request channel.",
                ephemeral=True
            )
            return
    await interaction.response.defer(ephemeral=True, thinking=True)
    reposted = 0
    for key, data in list(reqs.items()):
        try:
            if data.get("type") == "request":
                user_id = data.get("user_id")
                user_obj = await interaction.client.fetch_user(int(user_id))
                amount = data.get("amount")
                balance = data.get("balance")
                reason = data.get("reason")
                embed = discord.Embed(
                    title="Currency Request",
                    description=(
                        f"{user_obj.mention} is requesting {format_currency(amount, interaction.guild.id)}\n"
                        f"Balance: {balance.capitalize()}\n"
                        f"Reason: {reason}"
                    ),
                    color=discord.Color.gold()
                )
                embed.set_footer(text=f"Request | User: {user_id} | Balance: {balance} | Amount: {amount}")
            elif data.get("type") == "transfer":
                from_id = data.get("from")
                to_id = data.get("to")
                from_user = await interaction.client.fetch_user(int(from_id))
                to_user = await interaction.client.fetch_user(int(to_id))
                amount = data.get("amount")
                balance = data.get("balance")
                reason = data.get("reason")
                embed = discord.Embed(title="Currency Transfer Request", color=discord.Color.orange())
                embed.add_field(name="From", value=from_user.mention, inline=True)
                embed.add_field(name="To", value=to_user.mention, inline=True)
                embed.add_field(name="Amount", value=format_currency(amount, interaction.guild.id), inline=False)
                embed.add_field(name="Balance", value=balance.capitalize(), inline=False)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.set_footer(text=f"Transfer | From: {from_id} | To: {to_id} | Balance: {balance} | Amount: {amount}")
            else:
                continue
            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            reposted += 1
        except Exception as e:
            print(f"[rescan_requests] Failed to repost a request: {e}")
            continue
    await interaction.followup.send(
        f"🔄 Reposted {reposted} request(s).",
        ephemeral=True
    )


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Handle approval/denial of requests via reactions."""
    # Ignore bot's own reactions
    if payload.user_id == bot.user.id:
        return
    # Only respond to approval/denial emojis
    if str(payload.emoji) not in ("✅", "❌"):
        return
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    # Load config to check request channel and admin roles
    config = load_json(CONFIG_FILE)
    guild_cfg = config.get(str(payload.guild_id), {}) if payload.guild_id else {}
    req_channel_id = guild_cfg.get("request_channel")
    allowed_roles = guild_cfg.get("admin_roles", [])
    # Only handle reactions in the configured request channel
    if req_channel_id and payload.channel_id != req_channel_id:
        return
    # Fetch member to verify admin role
    guild = channel.guild
    member = guild.get_member(payload.user_id) if guild else None
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            member = None
    if not member:
        return
    if not any(role.id in allowed_roles for role in member.roles):
        return
    # Fetch the message and ensure it has an embed
    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return
    if not message.embeds:
        return
    embed = message.embeds[0]
    footer = embed.footer.text or ""
    reqs = load_json(REQUESTS_FILE)
    balances = load_json(BALANCES_FILE)
    history = load_json(HISTORY_FILE)
                                                 
                                                    
                                             
    # Process requests
    if footer.startswith("Request"):
                                                                                      
        try:
            uid = footer.split("User: ")[1].split(" |")[0]
            balance_type = footer.split("Balance: ")[1].split(" |")[0]
            amount = int(footer.split("Amount: ")[1])
        except Exception:
            return
                                        
        for key, data in list(reqs.items()):
            if data.get("type") == "request" and data.get("user_id") == uid and data.get("balance") == balance_type and data.get("amount") == amount:
                if str(payload.emoji) == "✅":
                                                           
                    user_bal = ensure_user_balances(uid)
                    user_bal[balance_type] = user_bal.get(balance_type, 0) + amount
                    balances[uid] = user_bal
                                     
                    entry = {
                        "type": "request",
                        "balance": balance_type,
                        "amount": amount,
                        "reason": data.get("reason", ""),
                        "by": "approval",
                    }
                    history.setdefault(uid, []).append(entry)
                    await channel.send(
                        f"✅ Approved {format_currency(amount, guild.id)} to <@{uid}> ({balance_type}). "
                        f"New {balance_type} balance: {format_currency(user_bal[balance_type], guild.id)}"
                    )
                else:
                    await channel.send(f"❌ Denied request by <@{uid}> (Balance: {balance_type}).")
                del reqs[key]
                break
    elif footer.startswith("Transfer"):
                                                                                                   
        try:
            from_id = footer.split("From: ")[1].split(" |")[0]
            to_id = footer.split("To: ")[1].split(" |")[0]
            balance_type = footer.split("Balance: ")[1].split(" |")[0]
            amount = int(footer.split("Amount: ")[1])
        except Exception:
            return
        for key, data in list(reqs.items()):
            if (
                data.get("type") == "transfer"
                and data.get("from") == from_id
                and data.get("to") == to_id
                and data.get("balance") == balance_type
                and data.get("amount") == amount
            ):
                if str(payload.emoji) == "✅":
                                                           
                    from_bal = ensure_user_balances(from_id)
                    to_bal = ensure_user_balances(to_id)
                    if from_bal.get(balance_type, 0) >= amount:
                        from_bal[balance_type] -= amount
                        to_bal[balance_type] = to_bal.get(balance_type, 0) + amount
                        balances[from_id] = from_bal
                        balances[to_id] = to_bal
                                          
                        history.setdefault(from_id, []).append({
                            "type": "transfer_out",
                            "balance": balance_type,
                            "amount": amount,
                            "reason": data.get("reason", ""),
                            "by": to_id,
                        })
                        history.setdefault(to_id, []).append({
                            "type": "transfer_in",
                            "balance": balance_type,
                            "amount": amount,
                            "reason": data.get("reason", ""),
                            "by": from_id,
                        })
                        await channel.send(
                            f"✅ Transfer approved! <@{from_id}> ➡️ <@{to_id}> "
                            f"{format_currency(amount, guild.id)} ({balance_type})"
                        )
                    else:
                        await channel.send(
                            f"❌ Transfer failed: <@{from_id}> doesn't have enough {balance_type} funds."
                        )
                else:
                    await channel.send(
                        f"❌ Transfer denied for <@{from_id}> ({balance_type})."
                    )
                del reqs[key]
                break
                     
    save_json(REQUESTS_FILE, reqs)
    save_json(BALANCES_FILE, balances)
    save_json(HISTORY_FILE, history)


bot.run(os.getenv("DISCORD_TOKEN"))
