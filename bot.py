import os
import re
import discord
from dotenv import load_dotenv
from google_sheets import append_inventory_row

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
INVENTORY_CHANNEL_ID = int(os.getenv("INVENTORY_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# Expected message format in the channel:
# borrow | person: X | device: Y | serial: Z | out: 2025-11-20 | back: 2025-11-25 | by: K

PATTERN = re.compile(
    r"borrow\s*\|\s*person:\s*(?P<person>[^|]+)\|"
    r"\s*device:\s*(?P<device>[^|]+)\|"
    r"\s*serial:\s*(?P<serial>[^|]+)\|"
    r"\s*out:\s*(?P<out>[^|]+)\|"
    r"\s*back:\s*(?P<back>[^|]+)\|"
    r"\s*by:\s*(?P<by>.+)",
    re.IGNORECASE
)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message):
    # ignore bot itself
    if message.author == client.user:
        return

    # only watch the inventory channel
    if message.channel.id != INVENTORY_CHANNEL_ID:
        return

    match = PATTERN.search(message.content)
    if not match:
        return

    data = match.groupdict()
    for k in data:
        data[k] = data[k].strip()

    message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

    try:
        append_inventory_row(
            person=data["person"],
            device=data["device"],
            serial=data["serial"],
            out_date=data["out"],
            back_date=data["back"],
            given_by=data["by"],
            message_link=message_link
        )
        await message.add_reaction("✅")
    except Exception as e:
        print("Error writing to sheet:", e)
        await message.add_reaction("⚠️")

client.run(TOKEN)