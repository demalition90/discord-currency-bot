import discord
from discord.ext import commands
import sqlite3
from datetime import datetime
import os

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

DB_FILE = "currency.db"
REQUEST_CHANNEL_ID = int(os.environ.get("REQUEST_CHANNEL_ID", "0"))

# Database setup
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

@bot.command()
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = get_balance(member.id)
    await ctx.send(f"{member.display_name} has {format_currency(balance)}")

@bot.command()
@commands.has_permissions(administrator=True)
async def give(ctx, member: discord.Member, amount: int, *, reason: str = "Manual adjustment"):
    update_balance(member.id, amount, reason, ctx.author.id)
    await ctx.send(f"Gave {format_currency(amount)} to {member.display_name} for: {reason}")

@bot.command()
@commands.has_permissions(administrator=True)
async def take(ctx, member: discord.Member, amount: int, *, reason: str = "Manual deduction"):
    update_balance(member.id, -amount, reason, ctx.author.id)
    await ctx.send(f"Took {format_currency(amount)} from {member.display_name} for: {reason}")

@bot.command()
async def request(ctx, amount: int, *, reason: str):
    channel = bot.get_channel(REQUEST_CHANNEL_ID)
    if channel is None:
        await ctx.send("Error: Request channel not found.")
        return
    embed = discord.Embed(title="Currency Request", description=f"{ctx.author.mention} is requesting {format_currency(amount)}\n**Reason:** {reason}", color=discord.Color.gold())
    embed.set_footer(text=f"User ID: {ctx.author.id} | Request Amount: {amount}")
    message = await channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO requests (user_id, amount, reason, message_id) VALUES (?, ?, ?, ?)",
              (ctx.author.id, amount, reason, message.id))
    conn.commit()
    conn.close()
    await ctx.send("Your request has been submitted for review.")

@bot.command()
async def transactions(ctx, member: discord.Member = None):
    member = member or ctx.author
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT amount, reason, admin_id, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5", (member.id,))
    results = c.fetchall()
    conn.close()
    if not results:
        await ctx.send("No transactions found.")
        return
    lines = []
    for amt, reason, admin_id, ts in results:
        lines.append(f"{format_currency(amt)} | {reason} | by <@{admin_id}> on {ts[:10]}")
    await ctx.send(f"**Last 5 transactions for {member.display_name}:**\n" + "\n".join(lines))

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) not in ["✅", "❌"]:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM requests WHERE message_id = ? AND status = 'pending'", (payload.message_id,))
    request_data = c.fetchone()
    if not request_data:
        conn.close()
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    if not member.guild_permissions.administrator:
        return
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    user_id, amount, reason = request_data[1], request_data[2], request_data[3]
    if str(payload.emoji) == "✅":
        update_balance(user_id, amount, reason, payload.user_id)
        status = "approved"
        await message.reply(f"✅ Approved by {member.display_name}. {format_currency(amount)} added.")
    elif str(payload.emoji) == "❌":
        status = "denied"
        await message.reply(f"❌ Denied by {member.display_name}.")
    c.execute("UPDATE requests SET status = ? WHERE message_id = ?", (status, payload.message_id))
    conn.commit()
    conn.close()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("DISCORD_BOT_TOKEN environment variable not set.")
