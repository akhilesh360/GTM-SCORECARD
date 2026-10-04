# HubSpot setup (free CRM)

HubSpot is the system of record. Everything here works on the **free** HubSpot CRM. One portal holds
both portfolio companies: `Company Type` says which company owns a record, and `GTM Motion` says which
of that company's pipelines a deal belongs to.

| Design element | Free CRM (default) | Paid upgrade path (optional) |
|---|---|---|
| Pipelines | One deal pipeline, **GTM Pipeline**, plus the `GTM Motion` dropdown (Inbound, Outbound, Referral, Emergency, Maintenance, Installation) | One pipeline per company motion (six). Set `hubspot.tier` to `"pro"` in `config/standard.json` |
| Lead routing | AI agent sets Territory and Assigned Rep from state | Territory routing workflow |
| SLA task | AI agent creates a 5-minute follow-up task on every new lead | SLA workflow with escalation |
| Lifecycle stage | Set on import from deal status | Lifecycle workflow on deal-stage change |

The tier switch changes the setup script and the import files; the data model, metrics and agent
logic stay the same.

## 0. Account and token

1. Sign in to the free HubSpot CRM.
2. Settings → Integrations → **Private Apps** → Create a private app. Scopes:
   `crm.objects.contacts.read/write`, `crm.objects.deals.read/write`,
   `crm.schemas.contacts.write`, `crm.schemas.deals.write`.
3. Copy the token into `.env` as `HUBSPOT_TOKEN=` (copy `.env.example` first; `.env` is git-ignored).

## 1. Properties and pipeline

```bash
python -m gtm.hubspot setup            # dry run: prints every API call
python -m gtm.hubspot setup --apply    # creates the property group and 12 properties, reshapes the default pipeline
python -m gtm.hubspot check            # lists pipelines and stages to confirm
```

On free, `--apply` renames the default deal pipeline to **GTM Pipeline** with stages
New (5%) → Contacted (10%) → Qualified (30%) → Proposal (60%) → Won → Lost. Run it on a fresh portal:
HubSpot refuses to drop stages that already hold deals. Re-running is safe; existing properties return
409 and only their dropdown options are refreshed.

Manual alternative: create what [properties.csv](properties.csv) lists (Settings → Properties) and edit the
default pipeline (Settings → Objects → Deals → Pipelines).

| Label | Internal name | Objects | Type |
|---|---|---|---|
| Lead Source | gtm_lead_source | contacts, deals | Dropdown: Google Ads, Meta Ads, Outbound, Referral, Call Tracking |
| UTM Source / Medium / Campaign | gtm_utm_* | contacts | Text |
| Company Type | gtm_company_type | contacts, deals | Dropdown: HVAC, MSP |
| Territory | gtm_territory | contacts, deals | Dropdown: territories + Unassigned |
| Fit Score | gtm_fit_score | contacts, deals | Number 0-100 (set by the AI agent) |
| Gross Margin | gtm_gross_margin | deals | Number (percent) |
| Service Type | gtm_service_type | contacts, deals | Dropdown: Emergency, Installation, Maintenance |
| Lead Created Date | gtm_lead_created_date | contacts | Date |
| Assigned Rep | gtm_assigned_rep | contacts, deals | Text |
| GTM Motion | gtm_motion | deals | Dropdown: Inbound, Outbound, Referral, Emergency, Maintenance, Installation |

Contacts get 10 custom properties and deals get 8. If your portal reports a custom-property limit, skip UTM Source and UTM Medium first; the model reads UTMs from the raw exports, not from HubSpot.

## 2. Import the data

**Free CRM caps contacts at 1,000.** The full files hold 4,785 contacts, so on free import the capped set in
`hubspot/import_sample/` instead: 450 contacts per company (900 total), every won deal kept, and every deal and
activity linked to a contact in the sample. Rebuild it with `python -m gtm.hubspot export --sample 450`.

Files are in `hubspot/import/` (full) and `hubspot/import_sample/` (free tier), one set per company. Import in this order (Contacts → Import →
*Start an import* → *File from computer*):

| Order | File | Import type | Notes |
|---|---|---|---|
| 1 | `<company>_contacts.csv` | One file, one object: Contacts | Columns auto-map by label. Set `Record ID (source)` to *Don't import* |
| 2 | `<company>_deals_with_contacts.csv` | One file, multiple objects: Contacts + Deals | Matches contacts on Email and associates the deal. Pipeline is `GTM Pipeline`, motion in `GTM Motion` |
| 3 | `<company>_activities_calls.csv` | One object: Calls | Associates to the contact by Email |
| 4 | `<company>_activities_meetings.csv` | One object: Meetings | Associates to the contact by Email |
| 5 | `<company>_activities_emails_as_notes.csv` | One object: Notes | Logged sales emails, kept as notes |

Spend, touchpoints and call-tracking stay in the model (`output/scorecard.db`); HubSpot holds the records reps work.

## 3. Saved views instead of pipelines

With one pipeline, give each team its own board:
Deals → board view → filter **Company Type** = MSP and **GTM Motion** = Inbound → *Save view* as
"Acme MSP – Inbound". Repeat for each motion. Reports filter on the same two properties.

## 4. Connect the AI agent (replaces workflows)

1. Run the agent: `python -m gtm.agent_server`, then expose it, e.g. `cloudflared tunnel --url http://localhost:5000`.
2. Private app → **Webhooks** → target URL `https://<tunnel>/webhooks/hubspot` → subscribe to `contact.creation`.
3. Put the app's client secret in `.env` as `HUBSPOT_CLIENT_SECRET=` so webhook signatures are verified.
4. Create a test contact with Company Type, Lead Source and State filled in. Within seconds it gets a
   Fit Score, Territory and Assigned Rep, a deal in GTM Pipeline with the right GTM Motion, a 5-minute
   follow-up task and an activity note.

The agent writes the rep's name to `Assigned Rep`. When reps exist as HubSpot users, pass their owner ids
(`HubSpotClient(owner_ids={"Dana Whitfield": "12345", ...})`) to assign deals and tasks to them directly.

## Optional: paid upgrade

A free 14-day Sales Hub Professional trial unlocks the six-pipeline layout and workflows:
set `"tier": "pro"` in `config/standard.json`, run `python run_all.py --skip-generate` and
`python -m gtm.hubspot setup --apply`, then build these workflows.

| Workflow | Trigger | Actions |
|---|---|---|
| Territory routing | Contact created, Territory known | Branch on Territory → set contact owner; Unassigned → rotate within the team |
| Lifecycle automation | Deal stage changes | Deal created → Sales Qualified Lead; Qualified → Opportunity; Won → Customer |
| SLA task | New contact, Lifecycle = Lead | Task "Follow up within 5 min" → wait 5 min → no activity → alert territory manager |
