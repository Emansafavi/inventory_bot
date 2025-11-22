import os
import re
import ssl
from datetime import datetime

import certifi

# Configure SSL to use certifi's certificate bundle (fixes macOS SSL issues)
# This must be done BEFORE importing discord/aiohttp
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

# Patch aiohttp to use certifi's certificates
import aiohttp

# Create SSL context with certifi's certificate bundle
ssl_context = ssl.create_default_context(cafile=certifi.where())

# Patch aiohttp's default connector to use our SSL context
original_connector_init = aiohttp.TCPConnector.__init__

def patched_connector_init(self, *args, **kwargs):
    if 'ssl' not in kwargs or kwargs['ssl'] is True:
        kwargs['ssl'] = ssl_context
    return original_connector_init(self, *args, **kwargs)

aiohttp.TCPConnector.__init__ = patched_connector_init

import discord
from dotenv import load_dotenv

from google_sheets import append_inventory_row, mark_return

# Load .env locally (Railway will ignore this and use its own env vars)
load_dotenv()

# Read env vars
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("INVENTORY_CHANNEL_ID")

print("Token length:", len(TOKEN) if TOKEN else "None")
print("Inventory channel raw:", CHANNEL_ID_RAW)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set or empty")

if not CHANNEL_ID_RAW:
    raise RuntimeError("INVENTORY_CHANNEL_ID is not set or empty")

try:
    INVENTORY_CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError("INVENTORY_CHANNEL_ID must be a numeric ID string")

# Discord intents
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# borrow | person: X | device: Y | serial: Z | out: 2025-11-20 | back: 2025-11-22 | by: K
BORROW_PATTERN = re.compile(
    r"borrow\s*\|\s*person:\s*(?P<person>[^|]+)\|"
    r"\s*device:\s*(?P<device>[^|]+)\|"
    r"\s*serial:\s*(?P<serial>[^|]+)\|"
    r"\s*out:\s*(?P<out>[^|]+)\|"
    r"\s*back:\s*(?P<back>[^|]+)\|"
    r"\s*by:\s*(?P<by>.+)",
    re.IGNORECASE,
)

# return | serial: Z | by: K
RETURN_PATTERN = re.compile(
    r"return\s*\|\s*serial:\s*(?P<serial>[^|]+)\|\s*by:\s*(?P<by>.+)",
    re.IGNORECASE,
)


def parse_date(date_str):
    date_str = date_str.strip()
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.date().isoformat()
    except ValueError:
        return None


BORROW_HELP = (
    "I could not read this. Use exactly this format:\n"
    "borrow | person: NAME | device: DEVICE | serial: SERIAL | "
    "out: YYYY-MM-DD | back: YYYY-MM-DD | by: NAME"
)

RETURN_HELP = (
    "I could not read this. Use exactly this format:\n"
    "return | serial: SERIAL | by: NAME"
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

    content_lower = message.content.lower()

    borrow_match = BORROW_PATTERN.search(message.content)
    return_match = RETURN_PATTERN.search(message.content)

    # Handle borrow
    if borrow_match:
        data = borrow_match.groupdict()
        for k in data:
            data[k] = data[k].strip()

        out_date = parse_date(data["out"])
        back_date = parse_date(data["back"])

        if out_date is None or back_date is None:
            await message.reply("Date format must be YYYY-MM-DD, for example: 2025-11-21.")
            await message.add_reaction("⚠️")
            return

        message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

        try:
            append_inventory_row(
                person=data["person"],
                device=data["device"],
                serial=data["serial"],
                out_date=out_date,
                back_date=back_date,
                given_by=data["by"],
                borrow_message_link=message_link,
            )
            await message.add_reaction("✅")
        except Exception as e:
            print("Error writing borrow to sheet:", e)
            await message.add_reaction("⚠️")

        return

    # Handle return
    if return_match:
        data = return_match.groupdict()
        for k in data:
            data[k] = data[k].strip()

        serial = data["serial"]
        returned_by = data["by"]
        message_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

        try:
            ok = mark_return(
                serial=serial,
                returned_by=returned_by,
                return_message_link=message_link,
            )
            if ok:
                await message.add_reaction("🔁")
            else:
                await message.reply(
                    "I could not find an open borrow entry for this serial number."
                )
                await message.add_reaction("⚠️")
        except Exception as e:
            print("Error writing return to sheet:", e)
            await message.add_reaction("⚠️")

        return

    # If user tries something starting with borrow or return but wrong format, give help
    if content_lower.startswith("borrow"):
        await message.reply(BORROW_HELP)
    elif content_lower.startswith("return"):
        await message.reply(RETURN_HELP)


client.run(TOKEN)