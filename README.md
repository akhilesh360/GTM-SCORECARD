# Portfolio GTM Standardization Scorecard (POC)

A reusable blueprint for measuring and automating go-to-market across portfolio companies.
Two synthetic archetypes are compared like for like:

| Company | Motion | Leads/mo | Lead→Opp | Opp→Won | ACV | Gross margin |
|---|---|---|---|---|---|---|
| Acme MSP (managed IT) | Inbound-heavy | 500 | 8% | 25% | $15k | 60% |
| Summit HVAC (commercial HVAC) | Outbound-heavy | 300 | 12% | 40% | $8k | 45% |

All companies, people and records are synthetic.

## Run it

```bash
pip install -r requirements.txt
python run_all.py            # data -> AI agent replay -> model -> HubSpot files -> dashboard
open dashboard/index.html    # executive dashboard (self-contained)
```

`python run_all.py --skip-generate` runs the same pipeline on whatever CSVs are already in `data/`
(for example real exports from a portfolio company).

## What's here

| Path | What it is |
|---|---|
| `config/standard.json` | The portfolio standard: channels, stages, opportunity rule, required fields, benchmarks, agent weights |
| `config/companies/*.json` | Everything company-specific: pipelines, territories and owners, ICP, economics, Slack channel |
| `gtm/generate.py` | Synthetic data generator (pandas + Faker) |
| `data/<company>/` | `leads`, `deals`, `spend`, `activities`, `call_tracking`, plus `touchpoints` and `deal_stage_history` |
| `sql/` | Channel funnel and multi-touch attribution SQL (SQLite) |
| `gtm/model.py` | Attribution and financial model: CAC, ROAS, LTV, payback, velocity, completeness |
| `output/` | Model results as CSV + `scorecard.db` (SQLite). These feed the dashboard and Looker Studio |
| `gtm/agent.py` | AI lead agent: enrich, score, route, create deal, SLA task, Slack alert, log activity |
| `gtm/agent_server.py` | Flask webhook endpoint (`/webhooks/hubspot`, `/leads`) |
| `gtm/agent_replay.py` | Replays the last 30 days of leads through the agent offline |
| `gtm/integrations.py` | Live HubSpot, Slack and OpenAI clients (used only when credentials are set) |
| `gtm/hubspot.py` | Generates HubSpot properties, pipelines and import files; can create them via API |
| `hubspot/` | Property and pipeline specs, import-ready CSVs, [SETUP.md](hubspot/SETUP.md) |
| `dashboard/` | Executive dashboard builder and output |
| `docs/` | [Looker Studio setup](docs/looker_studio.md), [adding a company](docs/add_a_company.md) |
| `playbook/` | Playbook source and PDF |

## The AI agent

```bash
python -m gtm.agent_replay                    # offline: mock HubSpot + mock Slack
python -m gtm.agent_server                    # webhook server on :5000
curl -X POST localhost:5000/webhooks/hubspot -H 'Content-Type: application/json' \
  -d '[{"objectId": "ACME-L03006", "subscriptionType": "contact.creation"}]'
```

Each integration switches to the live service when its credential is set. Put keys in `.env`
(copy `.env.example`; `.env` is git-ignored) or export them as environment variables:

| Variable | Effect |
|---|---|
| `HUBSPOT_TOKEN` | Read contacts and write deals, tasks and notes in HubSpot |
| `HUBSPOT_CLIENT_SECRET` | Verify HubSpot v3 webhook signatures |
| `SLACK_WEBHOOK_URL` | Post alerts to Slack |
| `DEEPSEEK_API_KEY` | Score leads with DeepSeek (`deepseek-chat`, JSON mode); falls back to the rules score |

## Metric definitions

Defined once in `gtm/model.py` and `config/standard.json`, identical for every company:

- **Opportunity**: a deal that has reached Qualified, including deals later lost.
- **Win rate**: won / closed opportunities.
- **Blended CAC**: total GTM spend (media, outbound team cost, referral payouts, call tracking) / won deals.
- **ROAS**: closed-won bookings / spend.
- **LTV**: ACV × gross margin × customer lifetime (lifetime set per company in config).
- **CAC payback**: CAC / (ACV × gross margin / 12), in months.
- **Pipeline velocity**: opportunities × win rate × ACV / sales-cycle days.
- **Data completeness**: share of lead and deal records with every required field populated.

## Pointing it at another company

Add `config/companies/<slug>.json` (pipelines, territories, ICP, economics) and drop that
company's CSVs into `data/<slug>/`. No code changes. See [docs/add_a_company.md](docs/add_a_company.md).
