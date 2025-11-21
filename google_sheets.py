import os
import json
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_PATH = "service_account.json"


def ensure_service_account_file():
    if os.path.exists(SERVICE_ACCOUNT_PATH):
        return

    json_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not json_content:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set in environment")

    data = json.loads(json_content)
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


def append_inventory_row(person, device, serial, out_date, back_date, given_by, borrow_message_link):
    sheet = get_sheet()
    timestamp = datetime.utcnow().isoformat()

    # columns:
    # A Timestamp
    # B Person
    # C Device
    # D Serial
    # E Out date
    # F Back date
    # G Given by
    # H Borrow message link
    # I Returned at
    # J Returned by
    # K Return message link
    sheet.append_row([
        timestamp,
        person,
        device,
        serial,
        out_date,
        back_date,
        given_by,
        borrow_message_link,
        "",
        "",
        "",
    ])


def mark_return(serial, returned_by, return_message_link):
    sheet = get_sheet()
    records = sheet.get_all_records()  # uses header row

    last_row_index = None

    # records is a list of dicts keyed by header names
    # row 1 is headers, so first record is row 2
    for idx, row in enumerate(records, start=2):
        row_serial = str(row.get("Serial", "")).strip()
        returned_at = str(row.get("Returned at", "")).strip()

        if row_serial == serial and not returned_at:
            last_row_index = idx

    if last_row_index is None:
        return False

    returned_at_ts = datetime.utcnow().isoformat()

    # Based on header structure: I, J, K are columns 9, 10, 11
    sheet.update_cell(last_row_index, 9, returned_at_ts)
    sheet.update_cell(last_row_index, 10, returned_by)
    sheet.update_cell(last_row_index, 11, return_message_link)

    return True