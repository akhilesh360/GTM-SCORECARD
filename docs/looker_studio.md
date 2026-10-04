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
| `channel_performance` | Channel performance |
| `attribution_long` | Attribution comparison |
| `ai_automation_impact`, `fit_score_validation`, `agent_runs` | AI automation impact |
| `call_tracking_impact` | Call-tracking impact |
| `stage_days`, `monthly_trend` | Velocity |
| `data_completeness` | Data quality |

Optional: Looker Studio's HubSpot partner connectors can read live CRM objects; the model outputs
remain the source for CAC, ROAS and attribution because those need spend and touch data HubSpot doesn't hold.

## 2. Report-level controls

- Add a **drop-down list** control on `portfolio_company` at the top of every page except Portfolio view.
- Theme: one accent color; channel colors fixed per channel (Google Ads blue, Meta Ads orange, Outbound teal,
  Referral yellow, Call Tracking pink) via *Dimension value colors* so they never change between charts.

## 3. Pages

**Portfolio view** (`scorecard`)
- Pivot table: Rows `category`, `metric`; Columns `portfolio_company`; Metric `value` (MAX).
- Add `definition` and `target` as row dimensions; conditional formatting on `status` (green/amber/red).
- Scorecard tiles: filter `metric` = Blended CAC, LTV : CAC, CAC payback, Bookings; one tile per company.

**Channel performance** (`channel_performance`)
- Three bar charts, dimension `channel`: metric `cac`, `roas`, `ltv_to_cac` (add a reference line at 3.0).
- Table: channel, total_spend, leads, opps, won, revenue, lead_to_opp, opp_to_won, cac, roas, ltv, cac_payback_months.

**Attribution comparison** (`attribution_long`)
- 100% stacked bar: dimension `model`, breakdown `channel`, metric `attributed_revenue`.
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
