"""Builds dashboard/index.html: a self-contained executive dashboard with the model outputs
embedded as JSON. Re-run (or run run_all.py) after the data changes and the dashboard refreshes."""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
HERE = Path(__file__).resolve().parent

TABLES = ["scorecard", "channel_performance", "attribution", "stage_days", "call_tracking_impact",
          "fit_score_validation", "monthly_trend", "data_completeness", "ai_automation_impact",
          "channel_monthly", "attribution_monthly", "cleaning_log"]


def records(name):
    p = OUT / f"{name}.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p)
    return json.loads(df.to_json(orient="records"))


def main():
    data = {t: records(t) for t in TABLES}
    data["run_info"] = json.loads((OUT / "run_info.json").read_text())
    data["standard"] = json.loads((ROOT / "config" / "standard.json").read_text())
    runs = OUT / "agent_runs.csv"
    if runs.exists():
        df = pd.read_csv(runs)
        sample = df.sort_values("fit_score", ascending=False).groupby("portfolio_company").head(3)
        data["agent_samples"] = json.loads(sample.to_json(orient="records"))
    outbox = OUT / "slack_outbox.jsonl"
    if outbox.exists():
        lines = [json.loads(l) for l in outbox.read_text().splitlines()]
        last_hot = {}
        for l in lines:
            if l["text"].startswith("HOT"):
                last_hot[l["channel"]] = l
        data["slack_samples"] = list(last_hot.values())
    import sys
    sys.path.insert(0, str(ROOT))
    from gtm.config import load_companies
    data["slack_channels"] = {c["name"]: c["slack_channel"] for c in load_companies()}
    data["company_meta"] = {c["name"]: f'{c["gtm_motion"]} {c["company_type"]}' for c in load_companies()}
    data["lifetime"] = {c["name"]: c["economics"]["customer_lifetime_years"] for c in load_companies()}
    html = (HERE / "template.html").read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    (HERE / "index.html").write_text(html)
    print(f"Wrote {HERE / 'index.html'} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
