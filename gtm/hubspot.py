"""HubSpot system-of-record configuration, generated from config so every portfolio company
gets the same schema.

  python -m gtm.hubspot export          write hubspot/*.csv (property + pipeline specs, import files)
  python -m gtm.hubspot setup           dry run: print the API payloads that would be sent
  python -m gtm.hubspot setup --apply   create property group, properties and pipeline(s) (needs HUBSPOT_TOKEN)
  python -m gtm.hubspot check           confirm the token works and list existing deal pipelines

Tier is set in config/standard.json -> hubspot.tier. "free" (default): one pipeline ("GTM Pipeline")
plus a GTM Motion dropdown, and the AI agent does routing and SLA tasks. "pro": one HubSpot
pipeline per company pipeline, plus the three workflows in hubspot/SETUP.md.

Custom properties are prefixed gtm_ so they never collide with HubSpot defaults; labels match
the design doc, which is what HubSpot's import tool auto-maps on.
"""
import argparse
import json
import os

import pandas as pd

from .config import DATA_DIR, ROOT, load_companies, load_standard, pipelines

HS_DIR = ROOT / "hubspot"
GROUP = {"name": "gtm_standard", "label": "GTM Standard (Portfolio)"}

# logical field used by the agent/model -> HubSpot internal property name
PROP_MAP = {
    "lead_source": "gtm_lead_source", "utm_source": "gtm_utm_source", "utm_medium": "gtm_utm_medium",
    "utm_campaign": "gtm_utm_campaign", "company_type": "gtm_company_type", "territory": "gtm_territory",
    "fit_score": "gtm_fit_score", "gross_margin": "gtm_gross_margin", "service_type": "gtm_service_type",
    "lead_created_date": "gtm_lead_created_date", "owner": "gtm_assigned_rep", "motion": "gtm_motion",
}


def motion_of(pipeline):
    """'Acme MSP - Inbound' -> 'Inbound'. On HubSpot free the motion replaces the pipeline."""
    return pipeline.split(" - ")[-1]


def hubspot_pipeline(pipeline, standard=None):
    """HubSpot pipeline label for a logical company pipeline, given the configured tier."""
    hs = (standard or load_standard())["hubspot"]
    return hs["free_pipeline"] if hs["tier"] == "free" else pipeline


def property_specs(companies=None, standard=None):
    companies = companies or load_companies()
    standard = standard or load_standard()
    territories = sorted({t for c in companies for t in c["territories"]}) + ["Unassigned"]
    services = sorted({r["service_type"] for c in companies for r in pipelines(c).values() if "service_type" in r})
    motions = list(dict.fromkeys(motion_of(p) for c in companies for p in pipelines(c)))

    def enum(name, label, values, objects, desc):
        return {"name": name, "label": label, "type": "enumeration", "fieldType": "select", "objects": objects,
                "description": desc,
                "options": [{"label": v, "value": v, "displayOrder": i} for i, v in enumerate(values)]}

    def simple(name, label, typ, field, objects, desc):
        return {"name": name, "label": label, "type": typ, "fieldType": field, "objects": objects, "description": desc}

    both = ["contacts", "deals"]
    return [
        enum("gtm_lead_source", "Lead Source", standard["channels"], both, "Standard portfolio channel (original source)"),
        simple("gtm_utm_source", "UTM Source", "string", "text", ["contacts"], "utm_source of the converting session"),
        simple("gtm_utm_medium", "UTM Medium", "string", "text", ["contacts"], "utm_medium of the converting session"),
        simple("gtm_utm_campaign", "UTM Campaign", "string", "text", ["contacts"], "e.g. msp_q3_search"),
        enum("gtm_company_type", "Company Type", sorted({c["company_type"] for c in companies}), both,
             "Which portfolio company owns the record"),
        enum("gtm_territory", "Territory", territories, both, "Sales territory, set by the AI agent from state"),
        simple("gtm_fit_score", "Fit Score", "number", "number", both, "0-100, set by the AI agent"),
        simple("gtm_gross_margin", "Gross Margin", "number", "number", ["deals"], "Expected gross margin, percent (0-100)"),
        enum("gtm_service_type", "Service Type", services, both, "HVAC service line; drives pipeline choice"),
        simple("gtm_lead_created_date", "Lead Created Date", "datetime", "date", ["contacts"],
               "Original lead creation time (kept separate from HubSpot createdate on import)"),
        simple("gtm_assigned_rep", "Assigned Rep", "string", "text", both,
               "Rep name from territory routing; map to HubSpot owners once users exist"),
        enum("gtm_motion", "GTM Motion", motions, ["deals"],
             "Company pipeline (motion). On HubSpot free it replaces separate pipelines"),
    ]


def pipeline_specs(companies=None, standard=None):
    companies = companies or load_companies()
    standard = standard or load_standard()
    prob = {"New": 0.05, "Contacted": 0.1, "Qualified": 0.3, "Proposal": 0.6, "Won": 1.0, "Lost": 0.0}
    stages = [{"label": s, "displayOrder": j, "metadata": {"probability": str(prob[s])}}
              for j, s in enumerate(standard["deal_stages"])]
    if standard["hubspot"]["tier"] == "free":
        return [{"label": standard["hubspot"]["free_pipeline"], "displayOrder": 0, "company": "All (free tier)",
                 "stages": stages}]
    return [{"label": name, "displayOrder": i, "company": c["name"], "stages": stages}
            for c in companies for i, name in enumerate(pipelines(c))]


def lifecycle(row):
    if row.get("stage") == "Won":
        return "customer"
    if row.get("stage_reached") in ("Qualified", "Proposal", "Won"):
        return "opportunity"
    if isinstance(row.get("deal_id"), str):
        return "salesqualifiedlead"
    return "lead"


def export():
    companies, standard = load_companies(), load_standard()
    (HS_DIR / "import").mkdir(parents=True, exist_ok=True)
    props = property_specs(companies, standard)
    pd.DataFrame([{"object": ", ".join(p["objects"]), "label": p["label"], "internal_name": p["name"],
                   "type": p["type"], "field_type": p["fieldType"],
                   "options": "; ".join(o["label"] for o in p.get("options", [])), "description": p["description"]}
                  for p in props]).to_csv(HS_DIR / "properties.csv", index=False)
    pd.DataFrame([{"company": p["company"], "pipeline": p["label"], "stage": s["label"], "stage_order": s["displayOrder"],
                   "probability": s["metadata"]["probability"]} for p in pipeline_specs(companies, standard)
                  for s in p["stages"]]).to_csv(HS_DIR / "pipelines.csv", index=False)

    for c in companies:
        leads = pd.read_csv(DATA_DIR / c["slug"] / "leads.csv", dtype=str)
        deals = pd.read_csv(DATA_DIR / c["slug"] / "deals.csv", dtype={"deal_id": str})
        merged = leads.merge(deals[["lead_id", "deal_id", "stage", "stage_reached"]], on="lead_id", how="left")
        names = leads.contact_name.str.split(" ", n=1, expand=True)
        contacts = pd.DataFrame({
            "First Name": names[0], "Last Name": names[1], "Email": leads.email, "Company Name": leads.company,
            "Job Title": leads.title, "Lead Source": leads.lead_source, "UTM Source": leads.utm_source,
            "UTM Medium": leads.utm_medium, "UTM Campaign": leads.utm_campaign, "Company Type": c["company_type"],
            "Territory": leads.territory.fillna("Unassigned"), "Fit Score": leads.fit_score,
            "Lead Created Date": leads.created_date.str[:10],
            "Lifecycle Stage": merged.apply(lifecycle, axis=1), "Record ID (source)": leads.lead_id,
        })
        contacts.to_csv(HS_DIR / "import" / f"{c['slug']}_contacts.csv", index=False)

        d = deals.merge(leads[["lead_id", "email"]], on="lead_id", how="left")
        service = d.pipeline.str.split(" - ").str[-1].where(d.pipeline.str.split(" - ").str[-1].isin(
            ["Emergency", "Maintenance", "Installation"]), "")
        pd.DataFrame({
            "Email": d.email, "Deal Name": d.company + " - " + d.pipeline.str.split(" - ").str[-1],
            "Pipeline": d.pipeline.map(lambda p: hubspot_pipeline(p, standard)),
            "GTM Motion": d.pipeline.map(motion_of), "Deal Stage": d.stage, "Amount": d.amount, "Close Date": d.close_date,
            "Lead Source": d.lead_source, "Company Type": c["company_type"],
            "Gross Margin": (d.gross_margin * 100).round(1), "Assigned Rep": d.owner, "Service Type": service,
            "Deal ID (source)": d.deal_id,
        }).to_csv(HS_DIR / "import" / f"{c['slug']}_deals_with_contacts.csv", index=False)

        # activities -> HubSpot Calls, Meetings and Notes imports, associated to contacts by email
        acts = pd.read_csv(DATA_DIR / c["slug"] / "activities.csv").merge(leads[["lead_id", "email"]], on="lead_id")
        call_out = {"connected": "Connected", "no_answer": "No answer", "voicemail": "Left voicemail"}
        meet_out = {"held": "Completed", "no_show": "No show", "rescheduled": "Rescheduled"}
        calls_ = acts[acts.type == "call"]
        pd.DataFrame({"Email": calls_.email, "Activity date": calls_.date, "Call outcome": calls_.outcome.map(call_out),
                      "Call title": "Sales call", "Activity ID (source)": calls_.activity_id}) \
            .to_csv(HS_DIR / "import" / f"{c['slug']}_activities_calls.csv", index=False)
        meets = acts[acts.type == "meeting"]
        pd.DataFrame({"Email": meets.email, "Meeting start time": meets.date, "Meeting outcome": meets.outcome.map(meet_out),
                      "Meeting name": "Discovery meeting", "Activity ID (source)": meets.activity_id}) \
            .to_csv(HS_DIR / "import" / f"{c['slug']}_activities_meetings.csv", index=False)
        emails = acts[acts.type == "email"]
        pd.DataFrame({"Email": emails.email, "Activity date": emails.date,
                      "Note body": "Sales email (" + emails.owner + "): " + emails.outcome,
                      "Activity ID (source)": emails.activity_id}) \
            .to_csv(HS_DIR / "import" / f"{c['slug']}_activities_emails_as_notes.csv", index=False)
    print(f"Wrote HubSpot specs and import files to {HS_DIR}")


def setup(apply=False):
    from .integrations import HUBSPOT_API, _http
    token = os.environ.get("HUBSPOT_TOKEN")
    if apply and not token:
        raise SystemExit("Set HUBSPOT_TOKEN (private app token) to apply; run without --apply for a dry run.")
    calls = []
    for obj in ("contacts", "deals"):
        calls.append(("POST", f"/crm/v3/properties/{obj}/groups", GROUP))
    for p in property_specs():
        body = {k: v for k, v in p.items() if k != "objects"}
        body["groupName"] = GROUP["name"]
        for obj in p["objects"]:
            calls.append(("POST", f"/crm/v3/properties/{obj}", body))
    free = load_standard()["hubspot"]["tier"] == "free"
    for p in pipeline_specs():
        body = {"label": p["label"], "displayOrder": p["displayOrder"], "stages": p["stages"]}
        # free tier allows one pipeline: reshape the default one instead of creating another
        calls.append(("PUT", "/crm/v3/pipelines/deals/default", body) if free else ("POST", "/crm/v3/pipelines/deals", body))
    for method, path, body in calls:
        if not apply:
            print(method, path, json.dumps(body)[:160])
            continue
        try:
            _http(method, HUBSPOT_API + path, token, body)
            print("ok  ", method, path, body.get("name") or body.get("label"))
        except Exception as e:
            if getattr(e, "code", None) == 409 and body.get("options"):
                # property exists: refresh its dropdown options so a new company's values appear
                _http("PATCH", f"{HUBSPOT_API}{path}/{body['name']}", token, {"options": body["options"]})
                print("upd ", "PATCH", path, body["name"])
            else:  # 409 on groups/pipelines = already exists; keep going so the script is re-runnable
                print("skip", method, path, body.get("name") or body.get("label"), getattr(e, "code", ""), e)
    print(f"{len(calls)} calls {'sent' if apply else 'planned (dry run)'}")


def check():
    from .integrations import HUBSPOT_API, _http
    res = _http("GET", HUBSPOT_API + "/crm/v3/pipelines/deals", os.environ["HUBSPOT_TOKEN"])
    for p in res.get("results", []):
        print(p["label"], "->", [s["label"] for s in p["stages"]])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "setup", "check"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"export": export, "setup": lambda: setup(a.apply), "check": check}[a.cmd]()
