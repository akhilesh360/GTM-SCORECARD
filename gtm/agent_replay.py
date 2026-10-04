"""Replay recent leads through the AI agent offline (mock HubSpot + mock Slack) to measure
automation impact. Writes output/agent_runs.csv, output/mock_crm/*.json, output/slack_outbox.jsonl.

  python -m gtm.agent_replay              last 30 days of leads, every company
  python -m gtm.agent_replay --days 7
  python -m gtm.agent_replay --llm-limit 50   with DEEPSEEK_API_KEY set: score up to 50 leads with DeepSeek
"""
import argparse
import csv
from datetime import datetime, timedelta

import pandas as pd

from .agent import LeadAgent
from .config import DATA_DIR, OUTPUT_DIR, load_companies
from .mocks import MockHubSpot, MockSlack


def main(days=30, llm_limit=25):
    companies = load_companies()
    outbox = OUTPUT_DIR / "slack_outbox.jsonl"
    outbox.unlink(missing_ok=True)
    crm, slack = MockHubSpot(), MockSlack(outbox)
    agent = LeadAgent(crm, slack, companies)
    llm_on = agent.use_llm
    runs = []
    for cfg in companies:
        crm.seed_contacts_from_csv(cfg["slug"])
        with open(DATA_DIR / cfg["slug"] / "leads.csv") as f:
            leads = list(csv.DictReader(f))
        cutoff = max(datetime.fromisoformat(l["created_date"]) for l in leads) - timedelta(days=days)
        for lead in leads:
            created = datetime.fromisoformat(lead["created_date"])
            if created >= cutoff:
                # cap paid LLM calls in a bulk replay; the rest use the rules score
                agent.use_llm = llm_on and sum(r["score_source"] == "deepseek" for r in runs) < llm_limit
                runs.append(agent.process_lead(dict(lead, company_slug=cfg["slug"]), now=created))
    crm.dump()
    df = pd.DataFrame(runs)
    df.to_csv(OUTPUT_DIR / "agent_runs.csv", index=False)
    print(df.groupby("portfolio_company").agg(leads=("lead_id", "count"), hot=("hot_lead", "sum"),
                                              median_sec=("processing_seconds", "median"),
                                              hours_saved=("minutes_saved", lambda s: s.sum() / 60)))
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--llm-limit", type=int, default=25, help="max leads scored by DeepSeek in this replay")
    a = ap.parse_args()
    main(a.days, a.llm_limit)
