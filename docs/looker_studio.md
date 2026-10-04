# Looker Studio dashboard

The HTML dashboard in `dashboard/index.html` is the reference build. This guide recreates it in
Looker Studio on top of the same model outputs, so it refreshes when the data does.

## 1. Data sources

1. Create a Google Sheet named **GTM Scorecard Data**.
2. Load the model outputs, one tab per file:
   - Quick start: File → Import → upload each CSV from `output/` → *Insert new sheet*.
   - Automatic: `python -m gtm.sheets_sync` (see the script header for the two env vars).
     Schedule `python run_all.py && python -m gtm.sheets_sync` and the dashboard never needs a manual refresh.
3. In Looker Studio: Create → Data source → **Google Sheets** → pick each tab. Use these tabs:

| Tab | Used on |
|---|---|
| `scorecard` | Portfolio view |
| `channel_monthly` | Channel performance (month grain, so the date filter works) |
| `attribution_monthly` | Attribution comparison (close-month grain) |
| `cleaning_log` | Data quality |
| `ai_automation_impact`, `fit_score_validation`, `agent_runs` | AI automation impact |
| `call_tracking_impact` | Call-tracking impact |
| `stage_days`, `monthly_trend` | Velocity |
| `data_completeness` | Data quality |

Optional: Looker Studio's HubSpot partner connectors can read live CRM objects; the model outputs
remain the source for CAC, ROAS and attribution because those need spend and touch data HubSpot doesn't hold.

## 2. Report-level controls

- Add three controls at the top of every page except Portfolio view: a **drop-down list** on
  `portfolio_company`, a **drop-down list** on `channel`, and a **date range control**.
  In each month-grain source, set `month` (or `close_month`) to type Date → Year Month so the date
  range applies.
- Theme: one accent color; channel colors fixed per channel (Google Ads blue, Meta Ads orange, Outbound teal,
  Referral yellow, Call Tracking pink) via *Dimension value colors* so they never change between charts.

## 3. Pages

**Portfolio view** (`scorecard`)
- Pivot table: Rows `category`, `metric`; Columns `portfolio_company`; Metric `value` (MAX).
- Add `definition` and `target` as row dimensions; conditional formatting on `status` (green/amber/red).
- Scorecard tiles: filter `metric` = Blended CAC, LTV : CAC, CAC payback, Bookings; one tile per company.

**Channel performance** (`channel_monthly`)
- Add calculated fields so they re-aggregate under any filter: `CAC = SUM(spend) / SUM(won)`,
  `ROAS = SUM(revenue) / SUM(spend)`, `Margin = SUM(margin_dollars) / SUM(margin_base)`.
- Bar charts, dimension `channel`: CAC, ROAS. Table: channel, spend, leads, opps, won, revenue, CAC, ROAS.
- `channel_performance` holds the full-period figures including LTV and payback, for a static table.

**Attribution comparison** (`attribution_monthly`)
- Three scorecards or a table by channel: SUM(first_touch_revenue), SUM(last_touch_revenue), SUM(linear_revenue).
- For the stacked bar by model, use `attribution_long` (full period): dimension `model`, breakdown `channel`, metric `attributed_revenue`.
- Table from `attribution`: channel × first_touch_roas, last_touch_roas, linear_roas.

**AI automation impact**
- Scorecards from `ai_automation_impact`: leads_processed, steps_automated, hours_saved,
  baseline_speed_to_lead_hours vs sla_minutes, hot_leads.
- Bar chart from `fit_score_validation`: `fit_band` × `lead_to_opp`.
- Table from `agent_runs`: company, fit_score, territory, owner, pipeline, rationale.

**Call-tracking impact** (`call_tracking_impact`)
- Table: source, channel, calls, booked_rate, missed_rate, revenue_influenced.
- Bar chart: source × missed_rate with a 10% reference line.

**Velocity and data quality**
- Pivot from `stage_days`: rows `pipeline`, columns `stage`, metric `avg_days`.
- Column charts from `monthly_trend`: month × leads, month × revenue.
- Table from `data_completeness`: object, field, completeness (bar-style conditional formatting).

## 4. Share

File → Share → *Anyone with the link can view*, or Download → PDF for the screenshot deliverable.
