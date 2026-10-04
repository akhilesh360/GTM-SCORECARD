"""Live integrations, used only when credentials are present.

  HUBSPOT_TOKEN       HubSpot private-app token (scopes: crm.objects.contacts.read/write,
                      crm.objects.deals.read/write, crm.schemas.contacts.write, crm.schemas.deals.write)
  SLACK_WEBHOOK_URL   Slack incoming-webhook URL
  DEEPSEEK_API_KEY    enables DeepSeek fit scoring (DEEPSEEK_MODEL, default deepseek-chat;
                      DEEPSEEK_BASE_URL, default https://api.deepseek.com)

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


def deepseek_score(lead, enriched, cfg, rule_score, components):
    """Ask DeepSeek (deepseek-chat, JSON mode) for a fit score, reason and territory.

    Returns {"fit_score": int, "reason": str, "territory": str|None} or None when no key is set,
    the call fails, or the answer is malformed, so the agent falls back to the rules score and
    never blocks on the model. The territory is validated by the caller against config.
    """
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    territories = {t: s["states"] for t, s in cfg["territories"].items()}
    system = ("You are a GTM operations agent for a private-equity portfolio company. "
              "Score inbound leads against the ideal customer profile. Return JSON only.")
    user = (
        f"Company: {cfg['name']} ({cfg['company_type']}, {cfg['gtm_motion']}).\n"
        f"ICP: industries {cfg['icp']['industries']}, {cfg['icp']['employees_min']}-{cfg['icp']['employees_max']} employees.\n"
        f"Territories (territory -> states): {json.dumps(territories)}. Use null if the state is in none.\n"
        f"Lead: {json.dumps({k: lead.get(k) for k in ('contact_name', 'title', 'company', 'lead_source', 'utm_campaign')})}\n"
        f"Enriched: {json.dumps({k: enriched[k] for k in ('industry', 'employees', 'annual_revenue', 'state')})}\n"
        f"Rules-based baseline score: {rule_score} (components 0-1: {json.dumps({k: round(v, 2) for k, v in components.items()})}).\n"
        'Score this lead 0-100 on ICP fit. Return json: {"fit_score": int, "reason": str (max 30 words, '
        'what the rep should lead with), "territory": str or null}'
    )
    try:
        res = _http("POST", f"{base}/chat/completions", key, {
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2, "max_tokens": 200,
        }, timeout=20)
        out = json.loads(res["choices"][0]["message"]["content"])
        score = int(round(float(out["fit_score"])))
        if not 0 <= score <= 100:
            return None
        return {"fit_score": score, "reason": str(out.get("reason") or "").strip(), "territory": out.get("territory")}
    except (urllib.error.URLError, KeyError, IndexError, TimeoutError, ValueError, TypeError):
        return None
