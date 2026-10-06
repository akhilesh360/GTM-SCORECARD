"""Tools the scorecard chat agent can run. Read-only, except add_test_lead, which creates ONE
contact + deal + task (live HubSpot when HUBSPOT_TOKEN is set, otherwise the mock CRM).
Nothing here deletes or bulk-writes.

TOOLS is the OpenAI/DeepSeek function-calling schema; run(name, args) executes one call.
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd

from .config import OUTPUT_DIR, load_companies, load_standard, pipeline_for, territory_for_state
from .enrich import mock_enrich
from .integrations import deepseek_score
from .scoring import score_fit

MODELS = {"first_touch": "first_touch", "last_touch": "last_touch", "linear": "linear"}
MAX_TEST_LEADS = 5  # per server run: the chat can never bulk-create records
_created = []


def _cfg(company):
    key = str(company or "").lower().replace(" ", "_")
    for c in load_companies():
        if key in (c["slug"], c["name"].lower().replace(" ", "_")) or key.split("_")[0] == c["slug"].split("_")[0]:
            return c
    raise ValueError(f"unknown company {company!r}; use one of {[c['name'] for c in load_companies()]}")


def compare_companies(metrics=None):
    df = pd.read_csv(OUTPUT_DIR / "scorecard_wide.csv")
    if metrics:
        wanted = [m.lower() for m in metrics]
        df = df[df.metric.str.lower().apply(lambda m: any(w in m for w in wanted))]
    return df[["metric", "unit", "Acme MSP", "Summit HVAC"]].round(3).to_dict("records")


def get_attribution(company, model="linear"):
    cfg = _cfg(company)
    m = MODELS.get(str(model).lower().replace("-", "_").replace(" ", "_"), "linear")
    df = pd.read_csv(OUTPUT_DIR / "attribution.csv")
    df = df[df.portfolio_company == cfg["name"]]
    return {"company": cfg["name"], "model": m,
            "channels": [{"channel": r.channel, "bookings": round(r[f"{m}_revenue"]), "spend": round(r.total_spend),
                          "roas": round(r[f"{m}_roas"], 2)} for _, r in df.iterrows()]}


def score_lead(company, lead_company, state, lead_source, industry=None, employees=None, title=None):
    """Score and route one lead exactly as the agent does (DeepSeek if keyed, rules otherwise). No writes."""
    cfg, std = _cfg(company), load_standard()
    e = mock_enrich(lead_company, cfg)
    e["state"] = str(state).upper()
    if industry:
        e["industry"] = industry
    if employees:
        e["employees"] = int(employees)
    rule, comps = score_fit(e, lead_source, cfg, std)
    lead = {"company": lead_company, "lead_source": lead_source, "title": title, "state": e["state"]}
    llm = deepseek_score(lead, e, cfg, rule, comps) if os.environ.get("DEEPSEEK_API_KEY") else None
    territory, owner = territory_for_state(cfg, e["state"])
    service = cfg.get("default_service_by_lead_source", {}).get(lead_source)
    score = llm["fit_score"] if llm else rule
    return {"company": cfg["name"], "lead": lead_company, "industry": e["industry"], "employees": e["employees"],
            "state": e["state"], "fit_score": score, "score_source": "deepseek" if llm else "rules",
            "rules_score": rule, "reason": (llm or {}).get("reason") or "", "hot": score >= std["agent"]["hot_lead_threshold"],
            "territory": territory or "Unassigned", "rep": owner, "pipeline": pipeline_for(cfg, lead_source, service),
            "sla_minutes": std["agent"]["sla_minutes"]}


def add_test_lead(company="Acme MSP"):
    """Create ONE test lead and run the agent on it. Live HubSpot if HUBSPOT_TOKEN is set, else mock."""
    if len(_created) >= MAX_TEST_LEADS:
        raise RuntimeError(f"limit of {MAX_TEST_LEADS} test leads per server run reached")
    from .agent import LeadAgent
    from .live_test import TEST_LEADS
    from .mocks import MockHubSpot, MockSlack
    cfg = _cfg(company)
    props = dict(TEST_LEADS[cfg["slug"]], email=f"test.lead+{int(time.time())}@example.com",
                 company_type=cfg["company_type"])
    live = bool(os.environ.get("HUBSPOT_TOKEN"))
    slack = MockSlack(OUTPUT_DIR / "slack_chat_test.jsonl")
    if live:
        from .integrations import HubSpotClient
        crm = HubSpotClient()
        contact_id = crm.create_contact(props)
        lead = crm.get_contact(contact_id)
    else:
        crm = MockHubSpot()
        contact_id = f"MOCK-{int(time.time())}"
        lead = dict(props, lead_id=contact_id, company_slug=cfg["slug"], created_date=datetime.now().isoformat())
        crm.update_contact(contact_id, lead)
    r = LeadAgent(crm, slack).process_lead(lead)
    _created.append(contact_id)
    return {"mode": "live HubSpot" if live else "mock CRM (no HUBSPOT_TOKEN)", "contact_id": contact_id,
            "deal_id": r["deal_id"], "task_id": r["task_id"], "company": cfg["name"], "lead": props["company"],
            "fit_score": r["fit_score"], "score_source": r["score_source"], "rules_score": r["rules_fit_score"],
            "reason": r["rationale"], "territory": r["territory"], "rep": r["owner"], "pipeline": r["pipeline"],
            "task_due": (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")}


_CO = {"type": "string", "enum": ["Acme MSP", "Summit HVAC"]}
_SRC = {"type": "string", "enum": ["Google Ads", "Meta Ads", "Outbound", "Referral", "Call Tracking"]}
TOOLS = [
    {"type": "function", "function": {
        "name": "compare_companies",
        "description": "Scorecard metrics side by side for both companies (bookings, CAC, LTV:CAC, payback, velocity, win rate, speed to lead, missed calls...). Optionally filter by metric name fragments.",
        "parameters": {"type": "object", "properties": {"metrics": {"type": "array", "items": {"type": "string"}}}}}},
    {"type": "function", "function": {
        "name": "get_attribution",
        "description": "Bookings, spend and ROAS per channel for one company under one attribution model.",
        "parameters": {"type": "object", "properties": {"company": _CO, "model": {"type": "string", "enum": list(MODELS)}},
                       "required": ["company", "model"]}}},
    {"type": "function", "function": {
        "name": "score_lead",
        "description": "Score and route one new lead the way the lead agent does: fit score (0-100), reason, territory, rep, pipeline and SLA. Read-only.",
        "parameters": {"type": "object", "properties": {
            "company": _CO, "lead_company": {"type": "string"}, "state": {"type": "string", "description": "2-letter US state"},
            "lead_source": _SRC, "industry": {"type": "string"}, "employees": {"type": "integer"}, "title": {"type": "string"}},
            "required": ["company", "lead_company", "state", "lead_source"]}}},
    {"type": "function", "function": {
        "name": "add_test_lead",
        "description": "Create ONE test lead in HubSpot (mock CRM if no token) and run the agent on it: contact, deal in the right pipeline, 5-minute SLA task. Use only when the user explicitly asks to add/create a test lead.",
        "parameters": {"type": "object", "properties": {"company": _CO}, "required": ["company"]}}},
]
_FUNCS = {"compare_companies": compare_companies, "get_attribution": get_attribution,
          "score_lead": score_lead, "add_test_lead": add_test_lead}


def run(name, args):
    if name not in _FUNCS:
        raise ValueError(f"unknown tool {name}")
    return _FUNCS[name](**(args or {}))
