"""Push output/*.csv into a Google Sheet (one tab per file) so Looker Studio refreshes on its own.

  export GOOGLE_SHEET_ID=<id from the sheet URL>
  export GOOGLE_ACCESS_TOKEN=$(gcloud auth print-access-token --scopes=https://www.googleapis.com/auth/spreadsheets)
  python -m gtm.sheets_sync

Schedule run_all.py followed by this script (cron, GitHub Actions, Cloud Scheduler) and the
dashboard stays current with no manual steps. Written against Sheets API v4; not yet run
against a live sheet in this POC.
"""
import csv
import json
import os
import urllib.parse

from .config import OUTPUT_DIR
from .integrations import _http

API = "https://sheets.googleapis.com/v4/spreadsheets"
TABS = ["scorecard", "scorecard_wide", "channel_performance", "attribution", "attribution_long", "stage_days",
        "call_tracking_impact", "fit_score_validation", "monthly_trend", "data_completeness",
        "ai_automation_impact", "agent_runs"]


def main():
    sheet, token = os.environ["GOOGLE_SHEET_ID"], os.environ["GOOGLE_ACCESS_TOKEN"]
    meta = _http("GET", f"{API}/{sheet}?fields=sheets.properties.title", token)
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    missing = [t for t in TABS if t not in existing and (OUTPUT_DIR / f"{t}.csv").exists()]
    if missing:
        _http("POST", f"{API}/{sheet}:batchUpdate", token,
              {"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]})
    for t in TABS:
        path = OUTPUT_DIR / f"{t}.csv"
        if not path.exists():
            continue
        with open(path) as f:
            rows = list(csv.reader(f))
        rng = urllib.parse.quote(f"'{t}'")
        _http("POST", f"{API}/{sheet}/values/{rng}:clear", token, {})
        _http("PUT", f"{API}/{sheet}/values/{rng}!A1?valueInputOption=USER_ENTERED", token, {"values": rows})
        print(f"{t}: {len(rows) - 1} rows")


if __name__ == "__main__":
    main()
