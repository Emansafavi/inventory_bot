import os
import re
import ssl
from datetime import datetime, date

import certifi

# Configure SSL to use certifi's certificate bundle (fixes macOS SSL issues)
# This must be done BEFORE importing discord/aiohttp
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# Patch aiohttp to use certifi's certificates
import aiohttp

# Create SSL context with certifi's certificate bundle
ssl_context = ssl.create_default_context(cafile=certifi.where())

# Patch aiohttp's default connector to use our SSL context
original_connector_init = aiohttp.TCPConnector.__init__


def patched_connector_init(self, *args, **kwargs):
    if "ssl" not in kwargs or kwargs["ssl"] is True:
        kwargs["ssl"] = ssl_context
    return original_connector_init(self, *args, **kwargs)


aiohttp.TCPConnector.__init__ = patched_connector_init

import discord
from discord.ext import tasks
from dotenv import load_dotenv

from google_sheets import append_inventory_row, mark_return

# Optional direct Google Sheets access for overdue checker
# Uses the same sheet as your existing integration
try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    gspread = None
    Credentials = None

# Load .env locally (Railway will ignore this and use its own env vars)
load_dotenv()

# Read env vars
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("INVENTORY_CHANNEL_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

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

# ------------- Overdue checker Google Sheets setup -------------

GS_WORKSHEET = None

if gspread is None or Credentials is None:
    print("gspread or google.oauth2 not available. Overdue checker disabled.")
else:
    if not GOOGLE_SHEET_ID:
        print("GOOGLE_SHEET_ID not set. Overdue checker disabled.")
    else:
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=scopes,
            )
            gs_client = gspread.authorize(creds)
            # Use the first worksheet. If your inventory is not the first tab,
            # change this to open by worksheet title.
            GS_WORKSHEET = gs_client.open_by_key(GOOGLE_SHEET_ID).sheet1
            print("Overdue checker: Google Sheets connection ok.")
        except Exception as e:
            print("Could not initialize Google Sheets for overdue checker:", e)
            GS_WORKSHEET = None


def _parse_sheet_date(value):
    if not value:
        return None
    text = str(value).strip()
    # Support ISO format first
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _find_column_key(row, keywords):
    """Return the first key whose name contains one of the given keywords."""
    for key in row.keys():
        kl = key.lower()
        for kw in keywords:
            if kw in kl:
                return key
    return None


def _row_is_returned(row):
    """Consider a row returned if there is a non empty status or return timestamp."""
    for key, value in row.items():
        kl = key.lower()
        if "status" in kl:
            v = str(value).strip().lower()
            if v in ("returned", "done", "closed"):
                return True
        if "returned" in kl:
            if str(value).strip():
                return True
        if "return" in kl and ("timestamp" in kl or "time" in kl or "date" in kl):
            # This is likely the actual return time, not the due date
            if str(value).strip():
                return True
    return False


def get_overdue_rows():
    """Return a list of dicts for overdue items.

    This assumes:
      - One column holds the due date, with a name containing 'back' or 'due'
      - A row is still open if it has no return timestamp or status as 'returned'
    """
    if GS_WORKSHEET is None:
        return []

    try:
        records = GS_WORKSHEET.get_all_records()
    except Exception as e:
        print("Error reading sheet for overdue check:", e)
        return []

    today = date.today()
    overdue = []

    for row in records:
        if _row_is_returned(row):
            continue

        # Find due date column (back or due)
        due_key = _find_column_key(row, ["back", "due"])
        if not due_key:
            # Fallback: treat a "return date" column as due date if no back/due exists
            due_key = _find_column_key(row, ["return date"])
        if not due_key:
            continue

        due_value = row.get(due_key)
        due_date = _parse_sheet_date(due_value)
        if not due_date:
            continue

        if due_date < today:
            overdue.append(row)

    return overdue


@tasks.loop(hours=24)
async def check_overdue():
    """Check the sheet once a day and send a message for overdue items."""
    if GS_WORKSHEET is None:
        return

    await client.wait_until_ready()
    channel = client.get_channel(INVENTORY_CHANNEL_ID)
    if channel is None:
        print("Overdue checker: inventory channel not found.")
        return

    overdue = await client.loop.run_in_executor(None, get_overdue_rows)

    if not overdue:
        return

    for row in overdue:
        person_key = _find_column_key(row, ["person", "name", "borrower"])
        device_key = _find_column_key(row, ["device", "item", "equipment"])
        serial_key = _find_column_key(row, ["serial", "id", "number"])
        due_key = _find_column_key(row, ["back", "due"]) or _find_column_key(row, ["return date"])

        person = (row.get(person_key) if person_key else "Unknown person") or "Unknown person"
        device = (row.get(device_key) if device_key else "Unknown device") or "Unknown device"
        serial = (row.get(serial_key) if serial_key else "Unknown serial") or "Unknown serial"
        due_value = row.get(due_key) if due_key else ""
        due_text = str(due_value).strip() if due_value is not None else "unknown date"

        msg = (
            f"⚠️ Rental period is over for **{person}** "
            f"on **{device}** (serial `{serial}`). "
            f"Due date was `{due_text}`. They have to hand it back."
        )
        try:
            await channel.send(msg)
        except Exception as e:
            print("Error sending overdue message:", e)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    # Start overdue checker loop if sheets is configured
    if GS_WORKSHEET is not None and not check_overdue.is_running():
        check_overdue.start()


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