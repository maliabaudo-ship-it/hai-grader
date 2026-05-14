#!/usr/bin/env python3
"""
One-time setup: populates the HAI Quality Rubric Google Sheet with all tabs.

Run once from Terminal:
    cd ~/hai-grader
    python3 populate_rubric_sheet.py

You'll be prompted to authorize in your browser the first time.
Credentials are cached at ~/.config/gspread/credentials.json for future runs.

Requirements:
    pip install gspread google-auth-oauthlib
"""

import json, time
from pathlib import Path

SHEET_ID = "1N16_ae-VIy0hWbJMpU-zDaBK4tqo4AflrZYUhVlz-0M"
DATA_FILE = Path("/tmp/rubric_data.json")

# ── Category → background color map (RGB tuples 0-1) ─────────────────────────
CAT_COLORS = {
    "Structure":   (0.851, 0.918, 0.827),
    "Polish":      (1.000, 0.949, 0.800),
    "Substance":   (0.988, 0.898, 0.804),
    "Occupation":  (0.816, 0.878, 0.953),
    "Penalty":     (0.957, 0.800, 0.800),
    "Bundle":      (0.902, 0.816, 0.871),
}
STATUS_COLORS = {
    "Active":   (0.878, 1.000, 0.878),
    "Watch":    (1.000, 0.992, 0.906),
    "Pending":  (1.000, 0.953, 0.878),
    "Rejected": (1.000, 0.922, 0.933),
}
HEADER_COLOR = (0.122, 0.306, 0.475)   # dark blue


def rgb(r, g, b):
    return {"red": r, "green": g, "blue": b}


def cell_fmt(bg_rgb=None, bold=False, wrap=True, h_align="LEFT", v_align="TOP"):
    fmt = {
        "textFormat": {"bold": bold, "fontFamily": "Arial", "fontSize": 9},
        "wrapStrategy": "WRAP" if wrap else "OVERFLOW_CELL",
        "horizontalAlignment": h_align,
        "verticalAlignment": v_align,
    }
    if bg_rgb:
        fmt["backgroundColor"] = rgb(*bg_rgb)
    return fmt


def build_requests(sheet_id_int, tab_name, rows, col_widths=None):
    """Build a list of Sheets API batchUpdate requests for one tab."""
    reqs = []
    n_cols = max(len(r) for r in rows) if rows else 1

    # Clear existing content
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id_int,
                  "startRowIndex": 0, "startColumnIndex": 0,
                  "endRowIndex": max(200, len(rows) + 5),
                  "endColumnIndex": n_cols},
        "fields": "userEnteredValue,userEnteredFormat"
    }})

    # Write rows
    row_data = []
    for r_idx, row in enumerate(rows):
        is_header = (r_idx == 0)
        cells = []
        for c_idx, val in enumerate(row):
            # Determine fill color
            if is_header:
                bg = HEADER_COLOR
                bold = True
                font_color = (1, 1, 1)
            else:
                bg = None
                bold = False
                font_color = (0, 0, 0)

                # Quality Rubric: color by category (col 0)
                if tab_name == "Quality Rubric" and r_idx > 0:
                    cat = rows[r_idx][0] if rows[r_idx] else ""
                    bg = CAT_COLORS.get(cat, (1, 1, 1))

                # Grader Learnings: color by status (col 6)
                if tab_name == "Grader Learnings" and r_idx > 0:
                    status = rows[r_idx][6] if len(rows[r_idx]) > 6 else ""
                    bg = STATUS_COLORS.get(status, (1, 1, 1))
                    if c_idx == 6:  # status column itself
                        bold = True

                if r_idx % 2 == 0 and bg is None:
                    bg = (0.949, 0.949, 0.949)

            fmt = {
                "textFormat": {
                    "bold": bold,
                    "fontFamily": "Arial",
                    "fontSize": 9 if not is_header else 10,
                    "foregroundColor": rgb(*font_color) if is_header else rgb(0, 0, 0),
                },
                "wrapStrategy": "WRAP",
                "verticalAlignment": "TOP",
                "horizontalAlignment": "CENTER" if (is_header or c_idx in (2, 3)) else "LEFT",
            }
            if bg:
                fmt["backgroundColor"] = rgb(*bg)

            cell = {"userEnteredFormat": fmt}
            if val:
                cell["userEnteredValue"] = {"stringValue": val}
            cells.append({"values": [cell]})

        row_data.append({"values": [c["values"][0] for c in cells]})

    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id_int,
                  "startRowIndex": 0, "startColumnIndex": 0},
        "rows": row_data,
        "fields": "userEnteredValue,userEnteredFormat"
    }})

    # Freeze header row
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id_int,
                       "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount"
    }})

    # Column widths
    if col_widths:
        for c_idx, px in enumerate(col_widths):
            reqs.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id_int, "dimension": "COLUMNS",
                          "startIndex": c_idx, "endIndex": c_idx + 1},
                "properties": {"pixelSize": px},
                "fields": "pixelSize"
            }})

    # Header row height
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id_int, "dimension": "ROWS",
                  "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 40},
        "fields": "pixelSize"
    }})

    return reqs


# ── Column widths per tab (pixels) ────────────────────────────────────────────
COL_WIDTHS = {
    "Quality Rubric":    [100, 260, 85, 130, 360, 395, 360],
    "Grader Learnings":  [85, 175, 100, 215, 430, 395, 85, 145],
    "Batch Performance": [175, 85, 75, 90, 90, 90, 90, 90, 90, 145, 85, 360],
    "Score Thresholds":  [75, 115, 290, 325],
    "Legend":            [100, 85, 145, 465],
}


def main():
    try:
        import gspread
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import pickle, os
    except ImportError:
        print("Missing deps. Run: pip install gspread google-auth-oauthlib")
        return

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dir = Path.home() / ".config" / "gspread"
    creds_dir.mkdir(parents=True, exist_ok=True)
    token_path = creds_dir / "token.pickle"
    client_secret_path = Path(__file__).parent / "credentials.json"

    creds = None
    if token_path.exists():
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_path.exists():
                print(f"\n  credentials.json not found at {client_secret_path}")
                print("  To set up:")
                print("  1. Go to https://console.cloud.google.com/")
                print("  2. Create a project > Enable Google Sheets API + Google Drive API")
                print("  3. Create OAuth 2.0 credentials (Desktop App)")
                print("  4. Download as credentials.json into ~/hai-grader/")
                print("  5. Re-run this script\n")
                return
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)
        print(f"Credentials saved to {token_path}")

    gc = gspread.authorize(creds)

    # Load data
    if not DATA_FILE.exists():
        print(f"Data file not found: {DATA_FILE}")
        print("Re-run the export step first.")
        return

    with open(DATA_FILE) as f:
        all_data = json.load(f)

    print(f"Populating sheet {SHEET_ID} ...")
    spreadsheet = gc.open_by_key(SHEET_ID)

    # Get existing sheets, rename/add as needed
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    desired_tabs = list(all_data.keys())

    # Rename Sheet1 → first tab
    if "Sheet1" in existing and desired_tabs[0] not in existing:
        existing["Sheet1"].update_title(desired_tabs[0])
        existing[desired_tabs[0]] = existing.pop("Sheet1")

    # Add missing tabs
    for tab in desired_tabs:
        if tab not in existing:
            ws = spreadsheet.add_worksheet(title=tab, rows=200, cols=15)
            existing[tab] = ws
            time.sleep(0.5)

    # Reorder tabs
    sheets_meta = spreadsheet.fetch_sheet_metadata()
    sheet_id_map = {s["properties"]["title"]: s["properties"]["sheetId"]
                    for s in sheets_meta["sheets"]}

    service = gc.auth  # underlying auth
    import googleapiclient.discovery as discovery
    sheets_svc = discovery.build("sheets", "v4", credentials=creds)

    # Reorder sheets
    reorder_reqs = []
    for i, tab in enumerate(desired_tabs):
        if tab in sheet_id_map:
            reorder_reqs.append({"updateSheetProperties": {
                "properties": {"sheetId": sheet_id_map[tab], "index": i},
                "fields": "index"
            }})
    if reorder_reqs:
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": reorder_reqs}
        ).execute()
        time.sleep(0.5)

    # Populate each tab
    all_requests = []
    for tab in desired_tabs:
        rows = all_data[tab]
        sid = sheet_id_map.get(tab)
        if sid is None:
            print(f"  Warning: sheet ID not found for tab '{tab}'")
            continue
        widths = COL_WIDTHS.get(tab)
        reqs = build_requests(sid, tab, rows, col_widths=widths)
        all_requests.extend(reqs)
        print(f"  Built requests for '{tab}' ({len(rows)} rows)")

    # Execute in batches of 50 requests
    batch_size = 50
    for i in range(0, len(all_requests), batch_size):
        batch = all_requests[i:i + batch_size]
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": batch}
        ).execute()
        print(f"  Batch {i // batch_size + 1}: {len(batch)} requests sent")
        time.sleep(0.3)

    print(f"\nDone! Sheet URL:")
    print(f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")


if __name__ == "__main__":
    main()
