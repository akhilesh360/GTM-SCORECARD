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
    # DeepSeek-scored leads: from this run's replay if it had a key, else the committed snapshot of a keyed run
    ai_runs = pd.read_csv(runs) if runs.exists() else pd.DataFrame()
    ai_runs = ai_runs[ai_runs.score_source == "deepseek"] if len(ai_runs) else ai_runs
    if not len(ai_runs):
        ai_runs = pd.read_csv(HERE / "agent_snapshot.csv")
    data["agent_ai"] = json.loads(ai_runs.to_json(orient="records"))
    live = OUT / "agent_live_runs.jsonl"
    data["agent_live"] = ([json.loads(l) for l in live.read_text().splitlines() if l.strip()] if live.exists()
                          else json.loads((HERE / "agent_live_runs.json").read_text()))
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
    data["icp"] = {c["name"]: c["icp"] for c in load_companies()}
    data["company_cfg"] = [{"name": c["name"], "motion": c["gtm_motion"], "type": c["company_type"],
                            "pipelines": [k for k in c["pipelines"] if not k.startswith("_")],
                            "territories": len(c["territories"]), "icp": c["icp"], "slack": c["slack_channel"]}
                           for c in load_companies()]
    data["lifetime"] = {c["name"]: c["economics"]["customer_lifetime_years"] for c in load_companies()}
    from gtm import chat
    data["chat"] = {"rules": chat.RULES, "context": chat.build_context()}
    html = (HERE / "template.html").read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    (HERE / "index.html").write_text(html)
    print(f"Wrote {HERE / 'index.html'} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
