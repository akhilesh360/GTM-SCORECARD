# HubSpot sandbox setup

HubSpot is the system of record. One portal holds both portfolio companies; the `Company Type`
property and the pipeline name keep them apart, so the same schema serves every company.

## 0. Account

- Start a **Sales Hub Professional trial** (free accounts allow one deal pipeline and no workflows;
  this POC needs six pipelines and three workflows).
- Settings → Integrations → **Private Apps** → Create. Scopes:
  `crm.objects.contacts.read/write`, `crm.objects.deals.read/write`,
  `crm.schemas.contacts.write`, `crm.schemas.deals.write`. Copy the token.

## 1. Properties and pipelines

Generated from config, so they always match the data model.

```bash
export HUBSPOT_TOKEN=pat-...
python -m gtm.hubspot setup            # dry run: prints the 25 API calls
python -m gtm.hubspot setup --apply    # creates the property group, 11 properties, 6 pipelines
python -m gtm.hubspot check            # lists pipelines and stages to confirm
```

Re-running is safe: anything that already exists returns 409 and is skipped.

Manual alternative: create what [properties.csv](properties.csv) and [pipelines.csv](pipelines.csv) list
(Settings → Properties, and Settings → Objects → Deals → Pipelines).

| Label | Internal name | Objects | Type |
|---|---|---|---|
| Lead Source | gtm_lead_source | contacts, deals | Dropdown: Google Ads, Meta Ads, Outbound, Referral, Call Tracking |
| UTM Source / Medium / Campaign | gtm_utm_* | contacts | Text |
| Company Type | gtm_company_type | contacts, deals | Dropdown: MSP, HVAC |
| Territory | gtm_territory | contacts, deals | Dropdown: territories + Unassigned |
| Fit Score | gtm_fit_score | contacts, deals | Number 0-100 (set by the AI agent) |
| Gross Margin | gtm_gross_margin | deals | Number (percent) |
| Service Type | gtm_service_type | contacts, deals | Dropdown: Emergency, Maintenance, Installation |
| Lead Created Date | gtm_lead_created_date | contacts | Date |
| Assigned Rep | gtm_assigned_rep | contacts, deals | Text |

Pipelines: Acme MSP – Inbound / Outbound / Referral; Summit HVAC – Emergency / Maintenance / Installation.
Stages in every pipeline: New → Contacted → Qualified → Proposal → Won → Lost.

## 2. Import the data

Files are in `hubspot/import/`. Import each company in this order:

1. **Contacts**: Contacts → Import → *File from computer* → *One file* → *One object* → Contacts →
   `<company>_contacts.csv`. Columns auto-map by label. Map `Record ID (source)` to *Don't import*
   or to a new text property if you want the source id kept.
2. **Deals with associations**: Import → *One file* → *Multiple objects* → Contacts + Deals →
   `<company>_deals_with_contacts.csv`. HubSpot matches the existing contact on `Email` and associates the deal.
   Choose *Update existing contacts*.

Activities, call-tracking and spend stay in the warehouse (`output/scorecard.db`); HubSpot holds
the CRM objects the reps touch.

## 3. Workflows (Automation → Workflows)

| Workflow | Trigger | Actions |
|---|---|---|
| **Territory routing** | Contact created, or Territory is known | Branch on Territory → set Contact owner (one branch per territory, owners from `config/companies/*.json`); Unassigned → rotate among the company's team |
| **Lifecycle automation** | Deal stage changes (deal-based) | Qualified → set associated contact Lifecycle Stage = Opportunity; Won → Customer; deal created → Sales Qualified Lead |
| **SLA task** | Contact created with Lifecycle Stage = Lead | Create task "Follow up within 5 min" for owner, due in 5 minutes → delay 5 min → if no call/email logged, send internal email/Slack to the territory manager |

When the AI agent is live it creates the deal and the SLA task itself; keep the SLA workflow as the
fallback for leads the agent could not process.

## 4. Point the webhook at the agent

1. Run the agent: `python -m gtm.agent_server` and expose it, e.g. `cloudflared tunnel --url http://localhost:5000`.
2. In the private app → **Webhooks** → target URL `https://<tunnel>/webhooks/hubspot` →
   subscribe to `contact.creation`. (On Operations Hub Pro, a workflow *Send a webhook* action works too.)
3. Set `HUBSPOT_CLIENT_SECRET` to the app's client secret so signatures are verified.
4. Create a test contact with Company Type, Lead Source and State filled in. Within seconds it gets a Fit Score
   and Territory, a deal in the right pipeline, a 5-minute task and an activity note.

Task owners: the agent writes the rep name to `Assigned Rep`. Once reps exist as HubSpot users, pass their
owner ids (`HubSpotClient(owner_ids={"Dana Whitfield": "12345", ...})`) to assign tasks and deals directly.
