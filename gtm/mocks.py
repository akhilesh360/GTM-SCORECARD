"""Offline stand-ins for HubSpot and Slack. Same method names as the real clients in
gtm/integrations.py, so the agent code does not change between demo and production."""
import itertools
import json
from datetime import datetime

from .config import DATA_DIR, OUTPUT_DIR


class MockHubSpot:
    """In-memory CRM. Contacts are seeded from data/<slug>/leads.csv so webhooks that only carry
    an objectId (as real HubSpot webhooks do) can be resolved."""

    def __init__(self, out_dir=None):
        self.out_dir = out_dir or OUTPUT_DIR / "mock_crm"
        self.contacts, self.deals, self.tasks, self.notes = {}, {}, {}, {}
        self._ids = itertools.count(900001)

    def _id(self):
        return str(next(self._ids))

    def seed_contacts_from_csv(self, slug):
        import csv
        with open(DATA_DIR / slug / "leads.csv") as f:
            for row in csv.DictReader(f):
                self.contacts[row["lead_id"]] = dict(row, company_slug=slug)

    def get_contact(self, contact_id):
        return self.contacts.get(str(contact_id))

    def update_contact(self, contact_id, properties):
        self.contacts.setdefault(str(contact_id), {}).update(properties)

    def create_deal(self, contact_id, properties):
        deal_id = self._id()
        self.deals[deal_id] = dict(properties, id=deal_id, contact_id=contact_id)
        return self.deals[deal_id]

    def create_task(self, deal_id, owner, subject, due_at, body=""):
        task_id = self._id()
        self.tasks[task_id] = {"id": task_id, "deal_id": deal_id, "owner": owner, "subject": subject,
                               "due_at": due_at.isoformat(), "body": body, "status": "NOT_STARTED"}
        return self.tasks[task_id]

    def create_note(self, contact_id, body, timestamp=None):
        note_id = self._id()
        self.notes[note_id] = {"id": note_id, "contact_id": contact_id, "body": body,
                               "timestamp": (timestamp or datetime.now()).isoformat()}
        return self.notes[note_id]

    def dump(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("deals", "tasks", "notes"):
            (self.out_dir / f"{name}.json").write_text(json.dumps(list(getattr(self, name).values()), indent=1))


class MockSlack:
    def __init__(self, outbox=None):
        self.outbox = outbox or OUTPUT_DIR / "slack_outbox.jsonl"
        self.outbox.parent.mkdir(parents=True, exist_ok=True)

    def send(self, channel, text):
        with open(self.outbox, "a") as f:
            f.write(json.dumps({"channel": channel, "text": text, "sent_at": datetime.now().isoformat()}) + "\n")
        return True
