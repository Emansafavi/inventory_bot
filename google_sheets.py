import os
import json
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_PATH = "service_account.json"


def ensure_service_account_file():
    # If the file already exists, nothing to do
    if os.path.exists(SERVICE_ACCOUNT_PATH):
        return

    json_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not json_content:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set in environment")

    # Parse the JSON string from the env variable
    data = json.loads(json_content)

    # Write it to service_account.json in the working directory
    with open(SERVICE_ACCOUNT_PATH, "w") as f:
        json.dump(data, f)


def get_sheet():
    ensure_service_account_file()

    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def append_inventory_row(person, device, serial, out_date, back_date, given_by, message_link):
    sheet = get_sheet()
    sheet.append_row([
        person,
        device,
        serial,
        out_date,
        back_date,
        given_by,
        message_link
    ])