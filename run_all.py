"""One command, end to end: data -> AI agent replay -> attribution/financial model -> HubSpot
import files -> dashboard. Re-run after changing data or config; every output refreshes.

  python run_all.py                 regenerate synthetic data too
  python run_all.py --skip-generate use the CSVs already in data/ (e.g. real exports)
"""
import argparse

from gtm import agent_replay, generate, hubspot, model
from dashboard import build as dashboard_build

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-generate", action="store_true")
    a = ap.parse_args()
    if not a.skip_generate:
        print("1/5 generating synthetic data");  generate.main()
    print("2/5 replaying last 30 days of leads through the AI agent");  agent_replay.main(30)
    print("3/5 running attribution & financial model");  model.main()
    print("4/5 exporting HubSpot specs and import files");  hubspot.export()
    print("5/5 building dashboard");  dashboard_build.main()
