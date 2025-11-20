import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1mgpGRz023-4bKuYUgCBcwwEpJ9McjaODc876Z9syUwM"

def get_sheet():
    creds = Credentials.from_service_account_file(
        "service_account.json",
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