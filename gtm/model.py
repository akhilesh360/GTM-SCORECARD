"""Attribution & financial model.

Loads every company's CSVs into one SQLite database (output/scorecard.db), runs the standard
SQL in sql/, then computes customer economics in pandas. Writes tidy CSVs to output/ that the
HTML dashboard and Looker Studio both read. Re-running this script is how the dashboard updates.
"""
import json
import sqlite3

import numpy as np
import pandas as pd

from .config import DATA_DIR, OUTPUT_DIR, ROOT, load_companies, load_standard

TABLES = ["leads", "deals", "spend", "activities", "call_tracking", "touchpoints", "deal_stage_history"]
DATE_COLS = {"leads": ["created_date"], "deals": ["created_date", "close_date"], "activities": ["date"],
             "call_tracking": ["call_datetime"], "touchpoints": ["touch_datetime"],
             "deal_stage_history": ["entered_at", "exited_at"]}


def load_frames(companies):
    frames = {t: [] for t in TABLES}
    for cfg in companies:
        for t in TABLES:
            path = DATA_DIR / cfg["slug"] / f"{t}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path)
            df.insert(0, "portfolio_company", cfg["name"])
            frames[t].append(df)
    out = {t: pd.concat(v, ignore_index=True) for t, v in frames.items() if v}
    for t, cols in DATE_COLS.items():
        for c in cols:
            if t in out:
                out[t][c] = pd.to_datetime(out[t][c])
    return out


def build_db(frames, path):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    for t, df in frames.items():
        d = df.copy()
        for c in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[c]):
                d[c] = d[c].dt.strftime("%Y-%m-%d %H:%M:%S")
        d.to_sql(t, con, index=False)
    return con


def safe_div(a, b):
    return float(a) / float(b) if b not in (0, None) and not pd.isna(b) else np.nan


def completeness(frames, cfg_name, standard):
    req = standard["required_fields"]
    leads = frames["leads"][frames["leads"].portfolio_company == cfg_name]
    deals = frames["deals"][frames["deals"].portfolio_company == cfg_name]
    rows = []

    def filled(series):
        return series.notna() & (series.astype(str).str.strip() != "")

    for f in req["leads"]:
        rows.append(("leads", f, filled(leads[f]).mean(), len(leads)))
    paid = leads[leads.lead_source.isin(standard["paid_ad_channels"])]
    for f in req["leads_paid_only"]:
        rows.append(("leads (paid ads)", f, filled(paid[f]).mean(), len(paid)))
    for f in req["deals"]:
        rows.append(("deals", f, filled(deals[f]).mean(), len(deals)))
    df = pd.DataFrame(rows, columns=["object", "field", "completeness", "records"])
    df.insert(0, "portfolio_company", cfg_name)
    # record-level: a lead/deal is complete only if every required field is populated
    lead_ok = pd.concat([filled(leads[f]) for f in req["leads"]], axis=1).all(axis=1)
    is_paid = leads.lead_source.isin(standard["paid_ad_channels"])
    paid_ok = pd.concat([filled(leads[f]) for f in req["leads_paid_only"]], axis=1).all(axis=1)
    lead_ok &= ~is_paid | paid_ok
    deal_ok = pd.concat([filled(deals[f]) for f in req["deals"]], axis=1).all(axis=1)
    record_rate = (lead_ok.sum() + deal_ok.sum()) / (len(leads) + len(deals))
    return df, record_rate


def run(companies=None):
    companies = companies or load_companies()
    standard = load_standard()
    OUTPUT_DIR.mkdir(exist_ok=True)
    frames = load_frames(companies)
    con = build_db(frames, OUTPUT_DIR / "scorecard.db")
    opp_stages = standard["opportunity_stages"]

    # ---------- channel performance (SQL + economics) ----------
    ch = pd.read_sql((ROOT / "sql" / "channel_funnel.sql").read_text(), con)
    deals = frames["deals"]
    won = deals[deals.stage == "Won"]
    life = {c["name"]: c["economics"]["customer_lifetime_years"] for c in companies}
    acv = won.groupby(["portfolio_company", "lead_source"]).amount.mean().rename("avg_acv")
    ch = ch.merge(acv, left_on=["portfolio_company", "channel"], right_index=True, how="left")
    ch["lead_to_opp"] = ch.opps / ch.leads.replace(0, np.nan)
    ch["opp_to_won"] = ch.won / ch.closed_opps.replace(0, np.nan)
    ch["ltv"] = ch.avg_acv * ch.avg_margin * ch.portfolio_company.map(life)
    ch["ltv_to_cac"] = ch.ltv / ch.cac
    ch["cac_payback_months"] = ch.cac / (ch.avg_acv * ch.avg_margin / 12)
    ch["spend_share"] = ch.total_spend / ch.groupby("portfolio_company").total_spend.transform("sum")
    ch["revenue_share"] = ch.revenue / ch.groupby("portfolio_company").revenue.transform("sum")

    # ---------- attribution ----------
    attr = pd.read_sql((ROOT / "sql" / "attribution.sql").read_text(), con)
    spend_c = ch.set_index(["portfolio_company", "channel"]).total_spend
    attr = attr.merge(spend_c, left_on=["portfolio_company", "channel"], right_index=True, how="right").fillna(
        {"first_touch_revenue": 0, "last_touch_revenue": 0, "linear_revenue": 0, "deals_touched": 0})
    for m in ("first_touch", "last_touch", "linear"):
        attr[f"{m}_roas"] = attr[f"{m}_revenue"] / attr.total_spend
    attr = attr.reset_index(drop=True)
    attr_long = attr.melt(id_vars=["portfolio_company", "channel"],
                          value_vars=["first_touch_revenue", "last_touch_revenue", "linear_revenue"],
                          var_name="model", value_name="attributed_revenue")
    attr_long["model"] = attr_long.model.str.replace("_revenue", "").str.replace("_", "-").str.title()

    # ---------- days in stage ----------
    hist = frames["deal_stage_history"].merge(deals[["deal_id", "portfolio_company", "pipeline"]],
                                              on=["deal_id", "portfolio_company"])
    hist = hist[hist.exited_at.notna()].copy()
    hist["days"] = (hist.exited_at - hist.entered_at).dt.total_seconds() / 86400
    stage_days = hist.groupby(["portfolio_company", "pipeline", "stage"]).days.agg(["mean", "median", "count"]) \
        .reset_index().rename(columns={"mean": "avg_days", "median": "median_days", "count": "deals"})
    stage_order = {s: i for i, s in enumerate(standard["deal_stages"])}
    stage_days = stage_days.sort_values(["portfolio_company", "pipeline", "stage"], key=lambda s: s.map(stage_order) if s.name == "stage" else s)

    # ---------- call tracking ----------
    calls = frames["call_tracking"]
    calls_src = calls.groupby(["portfolio_company", "channel", "source"]).agg(
        calls=("call_id", "count"),
        missed=("outcome", lambda s: (s == "missed").sum()),
        booked=("outcome", lambda s: (s == "answered_booked").sum()),
        avg_duration_sec=("duration", "mean"),
        revenue_influenced=("revenue_influenced", "sum"),
    ).reset_index()
    calls_src["missed_rate"] = calls_src.missed / calls_src.calls
    calls_src["booked_rate"] = calls_src.booked / calls_src.calls
    # revenue_influenced is per call; a won lead with two calls would be counted twice, so dedupe per lead
    lead_call = calls.groupby(["portfolio_company", "lead_id"]).agg(
        had_call=("call_id", "count"), any_missed=("outcome", lambda s: (s == "missed").any()))
    leads = frames["leads"].merge(lead_call, left_on=["portfolio_company", "lead_id"], right_index=True, how="left")
    leads["had_call"] = leads.had_call.fillna(0) > 0
    leads["any_missed"] = leads.any_missed.fillna(False).astype(bool)
    d_by_lead = deals.set_index(["portfolio_company", "lead_id"])
    leads = leads.merge(d_by_lead[["stage", "stage_reached", "amount"]], left_on=["portfolio_company", "lead_id"],
                        right_index=True, how="left")
    leads["is_opp"] = leads.stage_reached.isin(opp_stages)
    leads["is_won"] = leads.stage == "Won"

    # ---------- fit score validation ----------
    bands = pd.cut(leads.fit_score, [0, 40, 60, 80, 101], right=False, labels=["0-39", "40-59", "60-79", "80-100"])
    fit = leads.assign(fit_band=bands).groupby(["portfolio_company", "fit_band"], observed=True).agg(
        leads=("lead_id", "count"), opps=("is_opp", "sum"), won=("is_won", "sum")).reset_index()
    fit["lead_to_opp"] = fit.opps / fit.leads
    fit["lead_to_won"] = fit.won / fit.leads

    # ---------- monthly trend ----------
    sp = frames["spend"].groupby(["portfolio_company", "month"]).spend.sum()
    lm = frames["leads"].assign(month=frames["leads"].created_date.dt.strftime("%Y-%m")) \
        .groupby(["portfolio_company", "month"]).lead_id.count().rename("leads")
    wm = won.assign(month=won.close_date.dt.strftime("%Y-%m")).groupby(["portfolio_company", "month"]) \
        .agg(won=("deal_id", "count"), revenue=("amount", "sum"))
    monthly = pd.concat([sp, lm, wm], axis=1).fillna(0).reset_index()
    monthly["cac"] = monthly.spend / monthly.won.replace(0, np.nan)
    monthly["roas"] = monthly.revenue / monthly.spend

    # ---------- AI automation impact (from the agent replay, if it has been run) ----------
    runs_path = OUTPUT_DIR / "agent_runs.csv"
    runs = pd.read_csv(runs_path) if runs_path.exists() else pd.DataFrame()
    acts = frames["activities"].sort_values("date").groupby(["portfolio_company", "lead_id"]).date.first()
    stl = frames["leads"].set_index(["portfolio_company", "lead_id"]).created_date
    speed = ((acts - stl.reindex(acts.index)).dt.total_seconds() / 3600).rename("hours").reset_index()

    # ---------- portfolio scorecard ----------
    bm = standard["benchmarks"]
    rows = []
    comp_frames = []
    for cfg in companies:
        n = cfg["name"]
        L = frames["leads"][frames["leads"].portfolio_company == n]
        D = deals[deals.portfolio_company == n]
        W = D[D.stage == "Won"]
        opps = D[D.stage_reached.isin(opp_stages)]
        closed_opps = opps[opps.stage.isin(["Won", "Lost"])]
        spend_total = frames["spend"][frames["spend"].portfolio_company == n].spend.sum()
        revenue = W.amount.sum()
        acv_ = W.amount.mean()
        gm = safe_div((W.amount * W.gross_margin).sum(), W.loc[W.gross_margin.notna(), "amount"].sum())
        cac = safe_div(spend_total, len(W))
        ltv = acv_ * gm * cfg["economics"]["customer_lifetime_years"]
        cycle = (W.close_date - W.created_date).dt.total_seconds().mean() / 86400
        win_rate = safe_div(len(W), len(closed_opps))
        velocity = len(opps) * win_rate * acv_ / cycle
        C = calls[calls.portfolio_company == n]
        LC = leads[leads.portfolio_company == n]
        comp, record_rate = completeness(frames, n, standard)
        comp_frames.append(comp)
        sp_ = speed[speed.portfolio_company == n].hours
        R = runs[runs.portfolio_company == n] if len(runs) else pd.DataFrame()
        call_rev = LC[LC.had_call & LC.is_won].amount.sum()
        open_pipe = D[~D.stage.isin(["Won", "Lost"])].amount.sum()
        metrics = [
            ("Volume", "Leads", len(L), "count", "Leads created in period", None),
            ("Volume", "Opportunities", len(opps), "count", "Deals that reached Qualified", None),
            ("Volume", "Closed-won deals", len(W), "count", "Deals in Won stage", None),
            ("Volume", "Bookings (closed-won revenue)", revenue, "usd", "Sum of won deal amount (first-year contract value)", None),
            ("Volume", "Open pipeline", open_pipe, "usd", "Amount on deals not yet Won/Lost", None),
            ("Volume", "GTM spend", spend_total, "usd", "Media + outbound + referral + call-tracking spend", None),
            ("Conversion", "Lead to opportunity", safe_div(len(opps), len(L)), "pct", "Opportunities / leads", "lead_to_opp"),
            ("Conversion", "Opportunity to won", win_rate, "pct", "Won / closed opportunities (Won + Lost after Qualified)", "opp_to_won"),
            ("Conversion", "Speed to lead (median)", sp_.median(), "hours", "Lead created to first rep activity", "speed_to_lead_hours"),
            ("Economics", "Average contract value", acv_, "usd", "Mean amount of won deals", None),
            ("Economics", "Gross margin", gm, "pct", "Revenue-weighted margin on won deals", None),
            ("Economics", "Blended CAC", cac, "usd", "Total GTM spend / closed-won deals", None),
            ("Economics", "ROAS", safe_div(revenue, spend_total), "x", "Bookings / GTM spend", "roas"),
            ("Economics", "Customer lifetime (assumption)", cfg["economics"]["customer_lifetime_years"], "years", "Set per company in config (from churn)", None),
            ("Economics", "LTV", ltv, "usd", "ACV x gross margin x customer lifetime", None),
            ("Economics", "LTV : CAC", safe_div(ltv, cac), "x", "LTV / blended CAC", "ltv_to_cac"),
            ("Economics", "CAC payback", safe_div(cac, acv_ * gm / 12), "months", "CAC / (ACV x gross margin / 12)", "cac_payback_months"),
            ("Velocity", "Sales cycle", cycle, "days", "Deal created to Won, mean", None),
            ("Velocity", "Pipeline velocity", velocity, "usd_per_day", "Opps x win rate x ACV / sales cycle days", None),
            ("Data & Ops", "Data completeness", record_rate, "pct", "Share of lead + deal records with every required field populated", "data_completeness"),
            ("Data & Ops", "Inbound calls tracked", len(C), "count", "Calls logged by call tracking", None),
            ("Data & Ops", "Missed call rate", safe_div((C.outcome == "missed").sum(), len(C)), "pct", "Missed / tracked calls", "missed_call_rate"),
            ("Data & Ops", "Call-influenced bookings", safe_div(call_rev, revenue), "pct", "Share of bookings where the buyer phoned in at least once", None),
        ]
        if len(R):
            metrics += [
                ("AI Agent", "Leads processed by agent", len(R), "count", "Leads run through the AI agent end to end", None),
                ("AI Agent", "Manual steps eliminated", R.steps_automated.sum(), "count", "Automated steps x leads", None),
                ("AI Agent", "Rep hours saved", R.minutes_saved.sum() / 60, "hours", "Steps x manual minutes per step", None),
                ("AI Agent", "Routing time (agent, median)", R.processing_seconds.median(), "seconds", "Webhook received to deal + task created", None),
            ]
        for cat, name, val, unit, definition, bkey in metrics:
            status = ""
            if bkey and not pd.isna(val):
                b = bm[bkey]
                ok = val >= b["target"] if b["direction"] == "higher" else val <= b["target"]
                near = val >= b["target"] * 0.8 if b["direction"] == "higher" else val <= b["target"] * 1.25
                status = "green" if ok else ("amber" if near else "red")
            rows.append({"portfolio_company": n, "category": cat, "metric": name, "value": val, "unit": unit,
                         "definition": definition, "target": bm[bkey]["target"] if bkey else None, "status": status})
    scorecard = pd.DataFrame(rows)
    scorecard_wide = scorecard.pivot_table(index=["category", "metric", "unit", "definition"], columns="portfolio_company",
                                           values="value", aggfunc="first", sort=False).reset_index()
    completeness_df = pd.concat(comp_frames, ignore_index=True)

    # ---------- AI impact summary ----------
    ai = pd.DataFrame()
    if len(runs):
        base = speed.groupby("portfolio_company").hours.median().rename("baseline_speed_to_lead_hours")
        ai = runs.groupby("portfolio_company").agg(
            leads_processed=("lead_id", "count"), steps_automated=("steps_automated", "sum"),
            minutes_saved=("minutes_saved", "sum"), median_processing_seconds=("processing_seconds", "median"),
            hot_leads=("hot_lead", "sum"), sla_tasks_created=("task_id", "count"),
            unassigned_routed_to_queue=("territory", lambda s: (s == "Unassigned").sum()),
            avg_fit_score=("fit_score", "mean"), llm_rationales=("rationale_source", lambda s: (s == "llm").sum()),
        ).join(base).reset_index()
        ai["hours_saved"] = ai.minutes_saved / 60
        ai["sla_minutes"] = standard["agent"]["sla_minutes"]

    outputs = {
        "scorecard": scorecard, "scorecard_wide": scorecard_wide, "channel_performance": ch,
        "attribution": attr, "attribution_long": attr_long, "stage_days": stage_days,
        "call_tracking_impact": calls_src, "fit_score_validation": fit, "monthly_trend": monthly,
        "data_completeness": completeness_df, "ai_automation_impact": ai,
    }
    for name, df in outputs.items():
        if len(df):
            df.to_csv(OUTPUT_DIR / f"{name}.csv", index=False)
            df.to_sql(f"out_{name}", con, index=False, if_exists="replace")
    con.close()
    (OUTPUT_DIR / "run_info.json").write_text(json.dumps({
        "companies": [c["name"] for c in companies],
        "data_through": str(frames["leads"].created_date.max().date()),
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
    }, indent=2))
    return outputs


def main():
    out = run()
    sc = out["scorecard_wide"]
    with pd.option_context("display.width", 200, "display.max_columns", 10, "display.float_format", "{:,.2f}".format):
        print(sc.drop(columns=["definition"]).to_string(index=False))


if __name__ == "__main__":
    main()
