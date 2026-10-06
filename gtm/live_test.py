"""Send ONE new test lead through the agent against live HubSpot (and DeepSeek if keyed).

Creates 1 contact, then does what the webhook would: fetch it, score, route, create the deal,
SLA task and activity note. Prints every id created. Uses 1 of the free CRM's 1,000 contacts.

  python -m gtm.live_test                 Acme MSP test lead
  python -m gtm.live_test summit_hvac     Summit HVAC test lead
"""
import json
import os
import sys
import time
from datetime import datetime
import urllib.error

from .agent import LeadAgent
from .enrich import mock_enrich
from .config import OUTPUT_DIR, load_companies
from .integrations import DEEPSEEK_STATUS, HubSpotClient
from .mocks import MockSlack

TEST_LEADS = {
    "acme_msp": {"firstname": "Test", "lastname": "Lead", "company": "Harbor Point Dental Group",
                 "jobtitle": "Office Manager", "state": "NJ", "lead_source": "Google Ads"},
    "summit_hvac": {"firstname": "Test", "lastname": "Lead", "company": "Maple Ridge Property Management",
                    "jobtitle": "Facilities Director", "state": "TX", "lead_source": "Call Tracking"},
}


def main(slug="acme_msp"):
    if not os.environ.get("HUBSPOT_TOKEN"):
        print("HUBSPOT_TOKEN is not set. Add your private app token to .env.")
        return 1
    cfg = next(c for c in load_companies() if c["slug"] == slug)
    crm = HubSpotClient()
    agent = LeadAgent(crm, MockSlack(OUTPUT_DIR / "slack_live_test.jsonl"))
    print(f"DeepSeek: {'on' if agent.use_llm else 'off (no DEEPSEEK_API_KEY), rules score'}")
    props = dict(TEST_LEADS[slug], email=f"test.lead+{int(time.time())}@example.com",
                 company_type=cfg["company_type"])
    try:
        contact_id = crm.create_contact(props)
        print(f"contact  {contact_id}  {props['email']}")
        lead = crm.get_contact(contact_id)          # same path as the contact.creation webhook
        r = agent.process_lead(lead)
    except urllib.error.HTTPError as e:
        print(f"HubSpot error HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")
        return 1
    print(f"deal     {r['deal_id']}  GTM Pipeline / {r['pipeline'].split(' - ')[-1]} / New, rep {r['owner']}")
    print(f"task     {r['task_id']}  due {r['task_due'][:16]} (SLA 5 min)")
    print(f"score    {r['fit_score']} from {r['score_source']} (rules {r['rules_fit_score']}), territory {r['territory']}")
    print(f"reason   {r['rationale']}")
    if agent.use_llm and r["score_source"] != "deepseek":
        print(f"DeepSeek fell back to rules: {DEEPSEEK_STATUS['last_error']}")
    e = mock_enrich(props["company"], cfg)
    with open(OUTPUT_DIR / "agent_live_runs.jsonl", "a") as f:  # the dashboard shows these
        f.write(json.dumps({k: r[k] for k in ("portfolio_company", "company", "lead_source", "fit_score", "rules_fit_score",
                                              "score_source", "territory", "owner", "rationale")}
                           | {"industry": e["industry"], "employees": e["employees"],
                              "state": props["state"], "run_at": datetime.now().isoformat(timespec="seconds")}) + "\n")
    print(f"In HubSpot: Contacts -> search {props['email']} (deal, task and note are on the record)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
