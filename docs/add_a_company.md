# Adding a portfolio company

The success test for this blueprint: a new company needs only new data inputs and pipeline names.

## 1. Config

Copy `config/companies/acme_msp.json` to `config/companies/<slug>.json` and edit:

| Key | What to set |
|---|---|
| `slug`, `name`, `company_type`, `gtm_motion` | Identity. `company_type` becomes a HubSpot dropdown value |
| `economics.customer_lifetime_years` | From the company's churn (lifetime ≈ 1 / annual logo churn) |
| `pipelines` | Pipeline names and the rule that picks one (`lead_sources` list, or `service_type`) |
| `default_service_by_lead_source` | Only for service-type pipelines: fallback when a lead has no service type |
| `territories` | Territory → states and owning rep; `unassigned_owner` catches everything else |
| `icp` | Target industries and employee band (drives the fit score) |
| `slack_channel` | Where the agent posts new-lead alerts |
| `synthetic` | Only for demo data. Delete it for a real company |

`config/standard.json` stays untouched: channels, stages, the opportunity rule, required fields,
benchmarks and scoring weights are portfolio-wide.

## 2. Data

Export into `data/<slug>/raw/` with the standard column names. Values can be messy: the cleaning
step normalizes source names, dates, currency, margins and duplicates.

| File | Columns | Typical source |
|---|---|---|
| `leads.csv` | lead_id, company, contact_name, title, email, created_date, lead_source, utm_source, utm_medium, utm_campaign, territory, fit_score | CRM contacts |
| `deals.csv` | deal_id, lead_id, company, stage, amount, close_date, lead_source, gross_margin, pipeline, owner, created_date, stage_reached | CRM deals |
| `spend.csv` | channel, month, spend, impressions, clicks | Google Ads, Meta, payroll/tools for outbound |
| `activities.csv` | activity_id, lead_id, type, date, outcome, owner | CRM engagements |
| `call_tracking.csv` | call_id, lead_id, source, duration, outcome, revenue_influenced, call_datetime, channel | CallRail / CTM |
| `touchpoints.csv` | touch_id, lead_id, touch_datetime, channel, utm_campaign, touch_type | Web analytics / CRM page-view history |
| `deal_stage_history.csv` | deal_id, stage, entered_at, exited_at | CRM deal stage history |

If the cleaning log reports *Unknown source name*, add that name to `channel_aliases` in
`config/standard.json` and re-run.

## 3. Run

```bash
python run_all.py --skip-generate
python -m gtm.hubspot setup --apply     # adds the new dropdown values (and pipelines on the pro tier)
```

The scorecard, dashboard, HubSpot import files and agent routing all pick up the new company.
