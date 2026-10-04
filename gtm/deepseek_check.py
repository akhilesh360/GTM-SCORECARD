"""Send one lead through the agent and show whether DeepSeek scored it.

  python -m gtm.deepseek_check                 first Acme MSP lead
  python -m gtm.deepseek_check summit_hvac     first lead of another company
"""
import csv
import os
import sys
import time

from .agent import LeadAgent
from .config import DATA_DIR, OUTPUT_DIR, load_companies
from .integrations import DEEPSEEK_STATUS
from .mocks import MockHubSpot, MockSlack


def main(slug="acme_msp"):
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set. Add it to .env in the repo root.")
        return 1
    crm = MockHubSpot()
    crm.seed_contacts_from_csv(slug)
    agent = LeadAgent(crm, MockSlack(OUTPUT_DIR / "slack_check.jsonl"), load_companies())
    with open(DATA_DIR / slug / "leads.csv") as f:
        lead = next(csv.DictReader(f))
    t0 = time.perf_counter()
    r = agent.process_lead(dict(lead, company_slug=slug))
    (OUTPUT_DIR / "slack_check.jsonl").unlink(missing_ok=True)
    print(f"lead          {lead['lead_id']} ({r['portfolio_company']})")
    print(f"score_source  {r['score_source']}")
    print(f"fit_score     {r['fit_score']}  (rules score {r['rules_fit_score']})")
    print(f"territory     {r['territory']} ({r['territory_source']})")
    print(f"reason        {r['rationale']}")
    print(f"seconds       {time.perf_counter() - t0:.2f}")
    if r["score_source"] != "deepseek":
        print(f"DeepSeek failed, fell back to rules: {DEEPSEEK_STATUS['last_error']}")
        return 1
    print("DeepSeek is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
