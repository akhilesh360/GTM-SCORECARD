"""Flask webhook endpoint for the AI lead agent.

  POST /webhooks/hubspot   HubSpot webhook (contact.creation). Payload is a list of events that
                           carry only objectId; the agent fetches the contact, then processes it.
  POST /leads              Direct lead JSON (web form, call-tracking webhook, or a test).
  GET  /health

Run:  python -m gtm.agent_server            (mock HubSpot + mock Slack unless credentials are set)
Test: curl -X POST localhost:5000/webhooks/hubspot -H 'Content-Type: application/json' \
        -d '[{"objectId": "ACME-L03006", "subscriptionType": "contact.creation"}]'
"""
import base64
import hashlib
import hmac
import os
import time

from flask import Flask, abort, jsonify, request

from .agent import build_agent

app = Flask(__name__)
agent = build_agent()


def verify_hubspot_signature():
    """HubSpot v3 request signature. Enforced only when HUBSPOT_CLIENT_SECRET is set."""
    secret = os.environ.get("HUBSPOT_CLIENT_SECRET")
    if not secret:
        return
    ts = request.headers.get("X-HubSpot-Request-Timestamp", "0")
    sig = request.headers.get("X-HubSpot-Signature-v3", "")
    if abs(time.time() * 1000 - int(ts)) > 5 * 60 * 1000:
        abort(401, "stale webhook")
    raw = request.method + request.url + request.get_data(as_text=True) + ts
    expected = base64.b64encode(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(expected, sig):
        abort(401, "bad signature")


@app.post("/webhooks/hubspot")
def hubspot_webhook():
    verify_hubspot_signature()
    results = []
    for event in request.get_json(force=True) or []:
        if event.get("subscriptionType") != "contact.creation":
            continue
        contact = agent.crm.get_contact(event["objectId"])
        if not contact:
            results.append({"objectId": event["objectId"], "error": "contact not found"})
            continue
        contact.setdefault("lead_id", str(event["objectId"]))
        results.append(agent.process_lead(contact))
    _persist()
    return jsonify(results)


@app.post("/leads")
def direct_lead():
    lead = request.get_json(force=True)
    for field in ("lead_id", "company", "lead_source"):
        if not lead.get(field):
            abort(400, f"missing {field}")
    result = agent.process_lead(lead)
    _persist()
    return jsonify(result)


@app.get("/health")
def health():
    return {"ok": True, "crm": type(agent.crm).__name__, "slack": type(agent.slack).__name__,
            "llm": agent.use_llm}


def _persist():
    if hasattr(agent.crm, "dump"):
        agent.crm.dump()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
