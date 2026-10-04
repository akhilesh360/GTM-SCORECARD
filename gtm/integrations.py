"""Live integrations, used only when credentials are present.

  HUBSPOT_TOKEN       HubSpot private-app token (scopes: crm.objects.contacts.read/write,
                      crm.objects.deals.read/write, crm.schemas.contacts.write, crm.schemas.deals.write)
  SLACK_WEBHOOK_URL   Slack incoming-webhook URL
  OPENAI_API_KEY      enables the LLM scoring rationale (OPENAI_MODEL, default gpt-4o-mini)

NOTE: written against HubSpot CRM v3 public endpoints but not yet exercised against a live
portal in this POC; run `python -m gtm.hubspot check` first to confirm access.
"""
import json
import os
import urllib.error
import urllib.request

HUBSPOT_API = "https://api.hubapi.com"
# HubSpot-defined association type ids
DEAL_TO_CONTACT, TASK_TO_DEAL, NOTE_TO_CONTACT = 3, 216, 202


def _http(method, url, token=None, payload=None, timeout=15):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


class HubSpotClient:
    STD_PROPS = ["email", "firstname", "lastname", "company", "jobtitle", "state", "createdate"]

    def __init__(self, token=None, owner_ids=None):
        from .hubspot import PROP_MAP
        self.token = token or os.environ["HUBSPOT_TOKEN"]
        self.owner_ids = owner_ids or {}  # rep name -> HubSpot owner id
        self.prop_map = PROP_MAP           # logical field -> gtm_ internal property name
        self._pipelines = None

    def _call(self, method, path, payload=None):
        return _http(method, HUBSPOT_API + path, self.token, payload)

    def _props(self, logical):
        return {self.prop_map.get(k, k): str(v) for k, v in logical.items() if v is not None}

    def pipeline_ids(self):
        """{pipeline label: (pipeline id, {stage label: stage id})}"""
        if self._pipelines is None:
            res = self._call("GET", "/crm/v3/pipelines/deals")
            self._pipelines = {p["label"]: (p["id"], {s["label"]: s["id"] for s in p["stages"]})
                               for p in res.get("results", [])}
        return self._pipelines

    def get_contact(self, contact_id):
        props = self.STD_PROPS + list(self.prop_map.values())
        res = self._call("GET", f"/crm/v3/objects/contacts/{contact_id}?properties={','.join(props)}")
        p = res.get("properties", {})
        lead = {k: p.get(v) for k, v in self.prop_map.items()}
        lead.update({
            "lead_id": res.get("id"), "company": p.get("company"), "email": p.get("email"),
            "contact_name": f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip(),
            "title": p.get("jobtitle"), "state": p.get("state"), "created_date": p.get("createdate"),
        })
        return lead

    def update_contact(self, contact_id, properties):
        return self._call("PATCH", f"/crm/v3/objects/contacts/{contact_id}", {"properties": self._props(properties)})

    def create_deal(self, contact_id, properties):
        logical = dict(properties)
        pipe_id, stages = self.pipeline_ids()[logical.pop("pipeline")]
        stage_id = stages[logical.pop("dealstage")]
        props = self._props(logical)
        props.update({"pipeline": pipe_id, "dealstage": stage_id})
        if properties.get("owner") in self.owner_ids:
            props["hubspot_owner_id"] = self.owner_ids[properties["owner"]]
        res = self._call("POST", "/crm/v3/objects/deals", {
            "properties": props,
            "associations": [{"to": {"id": contact_id},
                              "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": DEAL_TO_CONTACT}]}],
        })
        return {"id": res["id"], **properties}

    def create_task(self, deal_id, owner, subject, due_at, body=""):
        props = {"hs_timestamp": due_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "hs_task_subject": subject,
                 "hs_task_body": body, "hs_task_status": "NOT_STARTED", "hs_task_priority": "HIGH"}
        if owner in self.owner_ids:
            props["hubspot_owner_id"] = self.owner_ids[owner]
        res = self._call("POST", "/crm/v3/objects/tasks", {
            "properties": props,
            "associations": [{"to": {"id": deal_id},
                              "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": TASK_TO_DEAL}]}],
        })
        return {"id": res["id"], "deal_id": deal_id, "owner": owner, "subject": subject}

    def create_note(self, contact_id, body, timestamp=None):
        from datetime import datetime, timezone
        ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        res = self._call("POST", "/crm/v3/objects/notes", {
            "properties": {"hs_timestamp": ts, "hs_note_body": body},
            "associations": [{"to": {"id": contact_id},
                              "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_CONTACT}]}],
        })
        return {"id": res["id"]}


class SlackWebhook:
    def __init__(self, url=None):
        self.url = url or os.environ["SLACK_WEBHOOK_URL"]

    def send(self, channel, text):
        # incoming webhooks post to the channel they were created for; channel is kept for the log
        _http("POST", self.url, payload={"text": text})
        return True


def llm_rationale(lead, enriched, fit_score, components, company_name):
    """One-sentence scoring rationale from an LLM. Returns None when no key is set or the call fails,
    so the agent never blocks on the model."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    prompt = (
        f"You are a RevOps assistant for {company_name}. In one sentence (max 30 words), explain to a sales rep "
        f"why this lead scored {fit_score}/100 and what to lead with on the first call.\n"
        f"Lead: {json.dumps({k: lead.get(k) for k in ('company', 'title', 'lead_source', 'utm_campaign')})}\n"
        f"Firmographics: {json.dumps({k: enriched[k] for k in ('industry', 'employees', 'state')})}\n"
        f"Score components (0-1): {json.dumps({k: round(v, 2) for k, v in components.items()})}"
    )
    try:
        res = _http("POST", "https://api.openai.com/v1/chat/completions", key, {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80, "temperature": 0.2,
        }, timeout=10)
        return res["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, TimeoutError, ValueError):
        return None
