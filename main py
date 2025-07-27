
import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction
import asyncio
import json
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# File to store balances and requests
BALANCES_FILE = "balances.json"
REQUESTS_FILE = "requests.json"

# Emojis
GOLD = ":g_:"
SILVER = ":s_:"
COPPER = ":c_:"

def load_balances():
    if not os.path.exists(BALANCES_FILE):
        return {}
    with open(BALANCES_FILE, "r") as f:
        return json.load(f)

def save_balances(balances):
    with open(BALANCES_FILE, "w") as f:
        json.dump(balances, f)

def load_requests():
    if not os.path.exists(REQUESTS_FILE):
        return {}
    with open(REQUESTS_FILE, "r") as f:
        return json.load(f)

def save_requests(requests):
    with open(REQUESTS_FILE, "w") as f:
        json.dump(requests, f)

def format_currency(value):
    gold = value // 10000
    silver = (value % 10000) // 100
    copper = value % 100
    return f"{gold}{GOLD}{silver:02}{SILVER}{copper:02}{COPPER}"

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

@bot.tree.command(name="balance", description="Check your balance.")
async def balance(interaction: Interaction):
    balances = load_balances()
    user_id = str(interaction.user.id)
    value = balances.get(user_id, 0)
    await interaction.response.send_message(f"{interaction.user.name} has {format_currency(value)}", ephemeral=False)

@bot.tree.command(name="give", description="Give currency to another user.")
@app_commands.describe(user="The user to give currency to", amount="Amount of copper to give")
async def give(interaction: Interaction, user: discord.User, amount: int):
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
        return

    balances = load_balances()
    sender_id = str(interaction.user.id)
    receiver_id = str(user.id)

    if balances.get(sender_id, 0) < amount:
        await interaction.response.send_message("You don't have enough funds.", ephemeral=True)
        return

    balances[sender_id] -= amount
    balances[receiver_id] = balances.get(receiver_id, 0) + amount
    save_balances(balances)

    await interaction.response.send_message(f"Transferred {format_currency(amount)} to {user.name}.")

@bot.tree.command(name="request", description="Request currency from the server.")
@app_commands.describe(amount="Amount in copper", reason="Reason for the request")
async def request(interaction: Interaction, amount: int, reason: str):
    requests = load_requests()
    request_id = str(interaction.id)
    user_id = str(interaction.user.id)
    requests[request_id] = {"user_id": user_id, "amount": amount, "reason": reason}
    save_requests(requests)

    embed = discord.Embed(title="Currency Request", description=f"{interaction.user.mention} is requesting {format_currency(amount)}
Reason: {reason}", color=0xF1C40F)
    embed.set_footer(text=f"User ID: {user_id} | Request Amount: {amount}")
    message = await interaction.channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    await interaction.response.send_message("Your request has been submitted for review.", ephemeral=False)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    if str(payload.emoji) not in ("✅", "❌"):
        return

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.embeds:
        embed = message.embeds[0]
        footer = embed.footer.text
        if footer and "User ID" in footer and "Request Amount" in footer:
            user_id = footer.split("User ID: ")[1].split(" |")[0]
            amount = int(footer.split("Request Amount: ")[1])
            requests = load_requests()
            for req_id, req in list(requests.items()):
                if req["user_id"] == user_id and req["amount"] == amount:
                    if str(payload.emoji) == "✅":
                        balances = load_balances()
                        balances[user_id] = balances.get(user_id, 0) + amount
                        save_balances(balances)
                        await channel.send(f"Request approved. {format_currency(amount)} granted to <@{user_id}>.")
                    else:
                        await channel.send(f"Request denied for <@{user_id}>.")
                    del requests[req_id]
                    save_requests(requests)
                    break

@bot.tree.command(name="transactions", description="View your transaction history.")
async def transactions(interaction: Interaction):
    await interaction.response.send_message("No transactions found.", ephemeral=False)

@bot.tree.command(name="rescan_requests", description="Rescan and process missed requests.")
async def rescan_requests(interaction: Interaction):
    requests = load_requests()
    processed = 0
    for req_id, req in list(requests.items()):
        user_id = req["user_id"]
        amount = req["amount"]
        channel = interaction.channel
        embed = discord.Embed(title="Currency Request", description=f"<@{user_id}> is requesting {format_currency(amount)}
Reason: {req.get('reason', 'N/A')}", color=0xF1C40F)
        embed.set_footer(text=f"User ID: {user_id} | Request Amount: {amount}")
        message = await channel.send(embed=embed)
        await message.add_reaction("✅")
        await message.add_reaction("❌")
        processed += 1

    await interaction.response.send_message(f"Rescan complete. {processed} requests processed.", ephemeral=False)

# Run the bot
if __name__ == "__main__":
    import asyncio
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    asyncio.run(bot.start(TOKEN))
