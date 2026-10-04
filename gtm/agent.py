"""AI lead agent: enrich -> score -> route -> create deal -> SLA task -> Slack alert -> log activity.

Runs with no accounts by default (MockHubSpot, MockSlack, template rationale). Set HUBSPOT_TOKEN,
SLACK_WEBHOOK_URL and/or OPENAI_API_KEY to switch each integration to the live service.
Everything company-specific (ICP, territories, owners, pipelines, Slack channel) comes from config.
"""
import os
import time
from datetime import datetime, timedelta

from .config import load_companies, load_standard, pipeline_for, territory_for_state
from .enrich import mock_enrich
from .integrations import llm_rationale
from .scoring import score_fit


def company_for_lead(lead, companies):
    """Pick the portfolio company config for a lead (by explicit slug, or by Company Type property)."""
    for cfg in companies:
        if lead.get("company_slug") == cfg["slug"] or lead.get("company_type") == cfg["company_type"]:
            return cfg
    raise ValueError(f"No portfolio company config matches lead {lead.get('lead_id')}")


def template_rationale(fit_score, comps, enriched, lead_source):
    strengths = [k.replace("_", " ") for k, v in comps.items() if v >= 0.8]
    gaps = [k.replace("_", " ") for k, v in comps.items() if v < 0.5]
    s = f"Fit {fit_score}/100: {enriched['industry']}, {enriched['employees']} employees, {enriched['state']}, via {lead_source}."
    if strengths:
        s += f" Strong on {', '.join(strengths)}."
    if gaps:
        s += f" Weak on {', '.join(gaps)}."
    return s


class LeadAgent:
    def __init__(self, crm, slack, companies=None, standard=None, use_llm=True):
        self.crm, self.slack = crm, slack
        self.companies = companies or load_companies()
        self.standard = standard or load_standard()
        self.use_llm = use_llm and bool(os.environ.get("OPENAI_API_KEY"))

    def process_lead(self, lead, now=None):
        """Process one new lead end to end. `now` lets a replay use the lead's creation time."""
        t0 = time.perf_counter()
        now = now or datetime.now()
        cfg = company_for_lead(lead, self.companies)
        std = self.standard["agent"]
        contact_id = lead["lead_id"]

        # 1. Enrich company (mock provider; swap for Apollo/Clearbit in production)
        enriched = mock_enrich(lead["company"], cfg)
        if lead.get("state"):
            enriched["state"] = lead["state"]

        # 2. Score fit (rules decide the number; the LLM only explains it)
        fit_score, comps = score_fit(enriched, lead["lead_source"], cfg, self.standard)
        rationale, rationale_source = None, "template"
        if self.use_llm:
            rationale = llm_rationale(lead, enriched, fit_score, comps, cfg["name"])
            rationale_source = "llm" if rationale else "template"
        rationale = rationale or template_rationale(fit_score, comps, enriched, lead["lead_source"])
        hot = fit_score >= std["hot_lead_threshold"]

        # 3. Assign territory and owner
        territory, owner = territory_for_state(cfg, enriched["state"])
        self.crm.update_contact(contact_id, {"fit_score": fit_score, "territory": territory or "Unassigned",
                                             "company_type": cfg["company_type"]})

        # 4. Create deal in the right pipeline
        service = lead.get("service_type") or cfg.get("default_service_by_lead_source", {}).get(lead["lead_source"])
        pipeline = pipeline_for(cfg, lead["lead_source"], service)
        deal = self.crm.create_deal(contact_id, {
            "dealname": f"{lead['company']} - {pipeline.split(' - ')[-1]}",
            "pipeline": pipeline, "dealstage": "New", "owner": owner,
            "lead_source": lead["lead_source"], "fit_score": fit_score, "territory": territory or "Unassigned",
        })

        # 5. SLA task: follow up within the portfolio SLA
        sla = std["sla_minutes"]
        task = self.crm.create_task(deal["id"], owner, f"Follow up within {sla} min: {lead['company']}",
                                    due_at=now + timedelta(minutes=sla), body=rationale)

        # 6. Slack alert to the company's lead channel
        flame = "HOT " if hot else ""
        self.slack.send(cfg["slack_channel"],
                        f"{flame}New lead: {lead['company']} | Fit: {fit_score} | {lead['lead_source']} | "
                        f"{territory or 'Unassigned'} -> {owner} | SLA {sla} min")

        # 7. Log activity on the contact timeline
        self.crm.create_note(contact_id, f"AI Agent processed lead. {rationale}", timestamp=now)

        steps = std["manual_minutes_per_step"]
        return {
            "portfolio_company": cfg["name"], "lead_id": contact_id, "company": lead["company"],
            "lead_source": lead["lead_source"], "fit_score": fit_score, "hot_lead": hot,
            "territory": territory or "Unassigned", "owner": owner, "pipeline": pipeline,
            "deal_id": deal["id"], "task_id": task["id"], "task_due": (now + timedelta(minutes=sla)).isoformat(),
            "rationale": rationale, "rationale_source": rationale_source,
            "steps_automated": len(steps), "minutes_saved": sum(steps.values()),
            "processing_seconds": round(time.perf_counter() - t0, 4),
        }


def build_agent():
    """Pick live or mock integrations based on which credentials are set."""
    from .integrations import HubSpotClient, SlackWebhook
    from .mocks import MockHubSpot, MockSlack
    if os.environ.get("HUBSPOT_TOKEN"):
        crm = HubSpotClient()
    else:
        crm = MockHubSpot()
        for cfg in load_companies():
            crm.seed_contacts_from_csv(cfg["slug"])
    slack = SlackWebhook() if os.environ.get("SLACK_WEBHOOK_URL") else MockSlack()
    return LeadAgent(crm, slack)
