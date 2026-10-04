"""One command, end to end: raw data -> cleaning/normalization -> AI agent replay ->
attribution/financial model -> HubSpot import files -> dashboard. Re-run after changing data or
config; every output refreshes.

  python run_all.py                 regenerate synthetic raw data too
  python run_all.py --skip-generate use the raw exports already in data/<company>/raw/
"""
import argparse

from gtm import agent_replay, clean, generate, hubspot, model
from dashboard import build as dashboard_build

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-generate", action="store_true")
    a = ap.parse_args()
    if not a.skip_generate:
        print("1/6 generating synthetic raw exports");  generate.main()
    print("2/6 cleaning and normalizing raw data");  clean.main()
    print("3/6 replaying last 30 days of leads through the AI agent");  agent_replay.main(30)
    print("4/6 running attribution & financial model");  model.main()
    print("5/6 exporting HubSpot specs and import files");  hubspot.export(); hubspot.export(sample=450)
    print("6/6 building dashboard");  dashboard_build.main()
