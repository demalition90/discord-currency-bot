import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime
import os

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

class CurrencyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="/", intents=intents)
        self.synced = False

    async def setup_hook(self):
        if not self.synced:
            await self.tree.sync()
            self.synced = True

bot = CurrencyBot()

DB_FILE = "currency.db"
REQUEST_CHANNEL_ID = int(os.environ.get("REQUEST_CHANNEL_ID", "0"))

# --- Database Setup ---
conn = sqlite3.connect(DB_FILE)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS balances (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT,
    admin_id INTEGER,
    timestamp TEXT
)""")
c.execute("""CREATE TABLE IF NOT EXISTS requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT,
    message_id INTEGER,
    status TEXT DEFAULT 'pending'
)""")
conn.commit()
conn.close()

def format_currency(copper: int) -> str:
    gold = copper // 10000
    silver = (copper % 10000) // 100
    copper = copper % 100
    return f"{gold}:g_:{silver:02}:s_:{copper:02}:c_:"

def update_balance(user_id: int, amount: int, reason: str, admin_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO balances (user_id, balance) VALUES (?, 0)", (user_id,))
    c.execute("UPDATE balances SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    c.execute("INSERT INTO transactions (user_id, amount, reason, admin_id, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, amount, reason, admin_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_balance(user_id: int) -> int:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

@bot.tree.command(name="balance")
@app_commands.describe(user="Leave blank to see your own balance")
async def balance(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    balance = get_balance(user.id)
    await interaction.response.send_message(f"{user.display_name} has {format_currency(balance)}")

@bot.tree.command(name="transactions")
async def transactions(interaction: discord.Interaction):
    user = interaction.user
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT amount, reason, admin_id, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user.id,))
    results = c.fetchall()
    conn.close()
    if not results:
        await interaction.response.send_message("No transactions found.")
        return
    lines = []
    for amt, reason, admin_id, ts in results:
        lines.append(f"{format_currency(amt)} | {reason} | by <@{admin_id}> on {ts[:10]}")
    await interaction.response.send_message("**Last 5 transactions:**\n" + "\n".join(lines))

@bot.tree.command(name="give")
@app_commands.checks.has_permissions(administrator=True)
async def give(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str):
    update_balance(member.id, amount, reason, interaction.user.id)
    await interaction.response.send_message(f"Gave {format_currency(amount)} to {member.display_name} for: {reason}")

@bot.tree.command(name="take")
@app_commands.checks.has_permissions(administrator=True)
async def take(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str):
    update_balance(member.id, -amount, reason, interaction.user.id)
    await interaction.response.send_message(f"Took {format_currency(amount)} from {member.display_name} for: {reason}")

@bot.tree.command(name="request")
async def request(interaction: discord.Interaction, amount: int, reason: str):
    channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("Error: Request channel not found.", ephemeral=True)
        return
    embed = discord.Embed(title="Currency Request", description=f"{interaction.user.mention} is requesting {format_currency(amount)}\n**Reason:** {reason}", color=discord.Color.gold())
    embed.set_footer(text=f"User ID: {interaction.user.id} | Request Amount: {amount}")
    message = await channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO requests (user_id, amount, reason, message_id) VALUES (?, ?, ?, ?)",
              (interaction.user.id, amount, reason, message.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message("Your request has been submitted for review.")

@bot.tree.command(name="rescan_requests")
@app_commands.checks.has_permissions(administrator=True)
async def rescan_requests(interaction: discord.Interaction):
    channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Request channel not found.", ephemeral=True)
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    rescanned = 0
    async for msg in channel.history(limit=100):
        if not msg.embeds or "Currency Request" not in msg.embeds[0].title:
            continue
        c.execute("SELECT * FROM requests WHERE message_id = ? AND status = 'pending'", (msg.id,))
        if not c.fetchone():
            continue
        reactions = {str(r.emoji): r.count for r in msg.reactions}
        if "✅" in reactions and reactions["✅"] > 1:
            embed = msg.embeds[0]
            uid_line = [line for line in embed.footer.text.split() if line.startswith("User")][0]
            user_id = int(uid_line.split()[2])
            amount = int(embed.footer.text.split("Amount: ")[-1])
            reason = embed.description.split("**Reason:** ")[-1]
            update_balance(user_id, amount, reason, interaction.user.id)
            await msg.reply(f"✅ Approved by {interaction.user.display_name}. {format_currency(amount)} added.")
            c.execute("UPDATE requests SET status = 'approved' WHERE message_id = ?", (msg.id,))
            rescanned += 1
        elif "❌" in reactions and reactions["❌"] > 1:
            await msg.reply(f"❌ Denied by {interaction.user.display_name}.")
            c.execute("UPDATE requests SET status = 'denied' WHERE message_id = ?", (msg.id,))
            rescanned += 1
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"Rescan complete. {rescanned} requests processed.")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("DISCORD_BOT_TOKEN not set.")