# GTM Scorecard: technical rundown

How the whole thing is built, module by module. State as of 2026-10-04 (repo `main`).

## 1. One command, six steps

```
python run_all.py                 # full run: generate synthetic raw data, then steps 2-6
python run_all.py --skip-generate # steps 2-6 on the raw exports already in data/<slug>/raw/
```

| Step | Module | Reads | Writes |
|---|---|---|---|
| 1 generate | `gtm/generate.py` | `config/companies/*.json` (`synthetic` block) | `data/<slug>/raw/*.csv` (messy exports) |
| 2 clean | `gtm/clean.py` | raw CSVs + `config/standard.json` aliases | `data/<slug>/*.csv` (clean), `output/cleaning_log.csv` |
| 3 agent replay | `gtm/agent_replay.py` | clean leads (last 30 days) | `output/agent_runs.csv`, `output/mock_crm/`, `output/slack_outbox.jsonl` |
| 4 model | `gtm/model.py` + `sql/*.sql` | clean CSVs + agent runs | `output/scorecard.db` (SQLite) + 13 CSVs + `run_info.json` |
| 5 HubSpot export | `gtm/hubspot.py export` | clean CSVs + config | `hubspot/properties.csv`, `pipelines.csv`, `import/*.csv` |
| 6 dashboard | `dashboard/build.py` | output CSVs | `dashboard/index.html` (self-contained) |

Separately: `python -m playbook.build` builds `playbook/GTM_Scorecard_Playbook.pdf` (HTML template + headless Chromium), and `python -m tests.test_pipeline` runs the tests.

Stack: Python 3, pandas, numpy, Faker, Flask, SQLite (stdlib). HTTP uses `urllib`, so there is no requests or openai dependency.

## 2. Config and portability

All company-specific data is config. Code never names a company.

- **`config/standard.json`** (portfolio-wide):
  - `channels`: Google Ads, Meta Ads, Outbound, Referral, Call Tracking.
  - `deal_stages`: New → Contacted → Qualified → Proposal → Won / Lost.
  - `opportunity_stages`: Qualified, Proposal, Won.
  - `required_fields` (drives completeness), `benchmarks` (target + direction for RAG status), `paid_ad_channels`.
  - `channel_aliases`, `stage_aliases`, `state_names` (cleaning maps).
  - `agent`: SLA 5 min; manual minutes per step, 17 in total; fit weights; source quality; hot threshold 80.
  - `hubspot.tier`: `free` or `pro`.
- **`config/companies/<slug>.json`** (per company):
  - `name`, `company_type`, `gtm_motion`.
  - `economics`: target ACV and margin, customer lifetime in years (Acme 4.0, Summit 3.0).
  - `pipelines`: each pipeline with the lead sources or service types that route to it.
  - `territories`: states plus owner rep; `unassigned_owner`.
  - `icp`: industries, employee band.
  - `slack_channel`.
  - `synthetic`: generator-only settings (seed, rates, channel mix, budgets, messiness).

Adding a company means one new config file plus `data/<slug>/raw/` exports. It needs no code edits. This was tested with a throwaway third company ("Peak Dental"). Steps are in `docs/add_a_company.md`.

## 3. Synthetic data (`gtm/generate.py`)

`generate_company(cfg)` builds clean "truth" frames from the config seed: leads, deals, deal_stage_history, spend (channel × month), activities, call_tracking and touchpoints. Window: Apr 1 to Sep 30, 2026. Win probability rises with fit score, which lets the fit-score validation show real lift.

`messify()` then degrades a copy into realistic raw exports. `messy_rate` is 0.18 for Acme and 0.35 for Summit. The damage includes:
- source aliases ("google cpc", "LSA", "FB")
- mixed date formats
- `$1,234` amounts
- margins as `61%`
- stage label drift
- casing and whitespace
- state names instead of codes
- missing fields
- 2% duplicate leads, with ids `PREFIX-L9xxxx`

## 4. Cleaning and normalization (`gtm/clean.py`)

Every rule logs to `cleaning_log.csv` with company, table, field, issue, rows_fixed and an example. The last run made 4,607 fixes for Acme and 5,577 for Summit, across 16 rules each.

| Rule | Function and logic |
|---|---|
| Channel names | `normalize_channel`: lowercase lookup in `channel_aliases` plus the canonical names; anything unknown becomes `Unmapped` |
| Money | `parse_money`: strip `$`, `,` and spaces, then `to_numeric` |
| Dates | `parse_dates`: `pd.to_datetime(format="mixed")`, logging any value that isn't ISO |
| Emails, UTMs | trim and lowercase; UTMs also turn spaces into `_` |
| Territory | state name → code via `state_names`; codes not in config → `Unassigned` |
| Duplicates | sort by `created_date`, `drop_duplicates("email", keep="first")` |
| Stages | `stage_aliases` map on `stage` and `stage_reached` |
| Margin | a value ending in `%` or greater than 1.5 is divided by 100 |
| Spend | re-aggregated to channel × month after aliasing merges rows |

`_fmt` writes date-only columns as `YYYY-MM-DD` and timestamps as `YYYY-MM-DD HH:MM:SS`. A test checks that cleaning recovers the generator's truth.

## 5. Model (`gtm/model.py`, `sql/`)

`build_db()` loads every clean table for all companies into `output/scorecard.db`, keyed by `portfolio_company`. The two SQL files run at company × month × channel grain, and Python then sums them to totals.

**`sql/channel_funnel.sql`** (CTEs `spend_c`, `leads_c`, `opps_c`, `won_c`) makes three deliberate corrections to the design doc's SQL:
1. Opportunities use `stage_reached IN ('Qualified','Proposal','Won')`. Filtering on the current stage would drop qualified deals that were later Lost, undercounting opportunities and inflating win rate.
2. Margin is averaged inside `won_c`, weighted by revenue. Joining deals a second time would fan out the rows and multiply spend.
3. Every CTE is keyed by company, so one query serves the whole portfolio.

**`sql/attribution.sql`**: the journey is every touchpoint on the deal's lead with `touch_datetime <= deal.created_date`, for Won deals.
- `ROW_NUMBER()` ascending and descending gives first and last touch.
- Linear gives each touch `amount / n_touches`.
- Each model sums back to total bookings, which a test checks.

**Formulas** (labels as they appear in `scorecard.csv`):

| Metric | Definition |
|---|---|
| Lead → opp | opportunities / leads |
| Opp → won | won / (Won + Lost among opportunities) |
| Speed to lead | median hours from lead created to first rep activity |
| ACV | mean won amount |
| Gross margin | revenue-weighted margin on won deals |
| Blended CAC | all GTM spend / won deals |
| ROAS | bookings / spend |
| LTV | ACV × GM × customer lifetime (config) |
| LTV : CAC | LTV / CAC |
| CAC payback (months) | CAC / (ACV × GM / 12) |
| Pipeline velocity | opps × win rate × ACV / mean sales-cycle days |
| Data completeness | share of lead + deal records with every required field filled (paid leads also need UTMs) |
| Missed call rate | missed / tracked calls |
| Call-influenced bookings | share of bookings where the buyer phoned in at least once |

**Outputs** in `output/`:
- `scorecard` (long form, with `index_to_target` and RAG status) and `scorecard_wide`
- `channel_performance`, `channel_monthly`
- `attribution`, `attribution_long`, `attribution_monthly`
- `monthly_trend`, `stage_days`, `call_tracking_impact`
- `fit_score_validation` (win rate by fit band)
- `data_completeness`, `ai_automation_impact`, `cleaning_log`
- `run_info.json`

## 6. AI lead agent (`gtm/agent.py`)

`LeadAgent.process_lead(lead)` does seven steps, each replacing a manual one (minutes saved per step come from config):

1. **Enrich**: `enrich.mock_enrich(company)` returns industry, employees, revenue and state. It is deterministic, seeded by sha256 of the company name. Swap in Apollo or Clearbit for production.
2. **Score**:
   - `scoring.score_fit` computes the rules score as 30·employee fit + 30·industry + 20·territory + 20·source quality. Employee fit decays with log distance outside the ICP band.
   - When `DEEPSEEK_API_KEY` is set, `integrations.deepseek_score` calls `deepseek-chat` in JSON mode with the ICP, territories, the lead, the enrichment and the rules score. It returns `{fit_score, reason, territory}`.
   - Any failure (HTTP error, timeout, bad JSON, out-of-range score) returns None, and the rules score is used. The error is recorded in `DEEPSEEK_STATUS`.
3. **Territory**: config is the source of truth. An LLM territory that disagrees with config is overridden, and `territory_source` records the override.
4. **Deal**: `config.pipeline_for(cfg, lead_source, service)` picks the pipeline. On the HubSpot free tier, the deal goes into "GTM Pipeline" with `gtm_motion` set.
5. **SLA task**: "Follow up within 5 min", due at created + 5 minutes, with the rationale in the body.
6. **Slack**: an alert to the company's channel, prefixed HOT when fit ≥ 80.
7. **Note**: "AI Agent processed lead..." on the contact timeline.

It returns fit_score, rules_fit_score, score_source, territory_source, rationale, steps_automated, minutes_saved and processing_seconds.

**Entry points:**
- `gtm/agent_server.py` (Flask):
  - `POST /webhooks/hubspot` handles `contact.creation`. It verifies the v3 signature when `HUBSPOT_CLIENT_SECRET` is set, fetches the contact and processes it.
  - `POST /leads` takes a raw JSON lead.
  - `GET /health`.
- `gtm/agent_replay.py`: replays the last 30 days of leads offline (824 leads), against mock CRM and Slack. DeepSeek attempts are capped by `--llm-limit` (default 25), and a summary line prints after the run.
- `gtm/deepseek_check.py`: sends one lead through the agent and prints score_source, score, reason and any API error.

`.env` is read by `config.load_dotenv()` at import. Real environment variables take precedence.

## 7. HubSpot (`gtm/hubspot.py`, `gtm/integrations.py`)

- `property_specs()` defines 12 properties in group "GTM Standard (Portfolio)", all prefixed `gtm_`: lead_source, utm source/medium/campaign, company_type, territory, fit_score, gross_margin, service_type, lead_created_date, assigned_rep and motion. Contacts get 10 and deals get 8.
- `pipeline_specs()`:
  - On free, it reshapes the default deal pipeline into "GTM Pipeline": New 5%, Contacted 10%, Qualified 30%, Proposal 60%, Won, Lost.
  - On `pro`, it creates one pipeline per company motion (six).
- `setup [--apply]` prints or executes the API calls. A 409 on an existing property patches its options instead, so re-running is safe. `check` lists the pipelines.
- `export` writes import-ready CSVs: contacts; deals with contacts (associated by email); calls; meetings; emails as notes.
- `HubSpotClient` (v3 REST) supports update_contact, create_deal, create_task and create_note. Association ids: deal→contact 3, task→deal 216, note→contact 202.

Spend, touchpoints and call tracking stay in the model. HubSpot holds the records reps work.

## 8. Dashboard and reporting

- `dashboard/index.html` is a single file with embedded JSON and hand-drawn SVG charts.
  - Filters: company, month range, channel chips. Filtered views recompute from `channel_monthly` and `attribution_monthly`.
  - Sections: portfolio view (scorecard tiles with RAG status), channel performance, attribution comparison, AI automation impact, call-tracking impact, pipeline velocity, and data quality with the cleaning log.
  - Published as an artifact.
- `gtm/sheets_sync.py` pushes the output tables to a Google Sheet (`GOOGLE_SHEET_ID`, `GOOGLE_ACCESS_TOKEN`) for Looker Studio. See `docs/looker_studio.md`.
- Playbook PDF (22 pages) and the 8-slide deck (`deck/`).

## 9. Tests (`tests/test_pipeline.py`)

- `test_cleaning_recovers_truth`: messify, then clean, matches the generator's truth.
- `test_attribution_models_conserve_bookings`: first, last and linear each sum to total bookings.
- `test_agent_falls_back_without_llm`: with no key, the agent uses the rules score and still completes every step.

## 10. Live vs mocked

| Piece | Today | Goes live when |
|---|---|---|
| Data | Synthetic (Faker, seeded) | Real exports dropped into `data/<slug>/raw/` |
| DeepSeek scoring | Live code, verified against a fake server | `DEEPSEEK_API_KEY` in `.env` (check with `python3 -m gtm.deepseek_check`) |
| HubSpot | `MockHubSpot` writes to `output/mock_crm/` | `HUBSPOT_TOKEN` in `.env`, then `python -m gtm.hubspot setup --apply` and the CSV imports |
| Slack | `MockSlack` writes to `output/slack_outbox.jsonl` | `SLACK_WEBHOOK_URL` |
| Enrichment | Deterministic mock | Swap `mock_enrich` for a real provider |
| Sheets / Looker | Code ready | `GOOGLE_SHEET_ID` + `GOOGLE_ACCESS_TOKEN` |

None of the live clients has run against a real account yet.

## 11. Pending on your side

1. Run `python3 -m gtm.deepseek_check` and confirm it prints `score_source deepseek`.
2. HubSpot: create the private-app token, put it in `.env`, run `setup --apply` (or set up by hand following `hubspot/SETUP.md`), then import the CSVs.
3. Create a Google Sheet, connect Looker Studio, and run `gtm/sheets_sync.py` if you want live tables.
4. Optional: a Slack webhook.
5. Record the Loom walkthrough using the deck.
