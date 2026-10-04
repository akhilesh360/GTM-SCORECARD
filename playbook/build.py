"""Builds the playbook: playbook/playbook.html (source) -> playbook/GTM_Scorecard_Playbook.pdf.
Tables, numbers and code listings are pulled from config, model outputs and source files, so the
playbook never drifts from the system it documents.

  python -m playbook.build
"""
import base64
import html
import json
import re
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
OUT = ROOT / "output"
CHROMIUM = "/opt/pw-browsers/chromium"

import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from gtm.config import load_companies, load_standard, pipelines  # noqa: E402
from gtm.hubspot import property_specs  # noqa: E402


def font_css():
    faces = [("Archivo", "600 700", "normal", "Archivo-600.woff2"),
             ("Public Sans", "400 700", "normal", "PublicSans-400.woff2"),
             ("Public Sans", "400", "italic", "PublicSans-400i.woff2"),
             ("JetBrains Mono", "400", "normal", "JetBrainsMono-400.woff2")]
    css = []
    for fam, w, st, fn in faces:
        b64 = base64.b64encode((HERE / "fonts" / fn).read_bytes()).decode()
        css.append(f"@font-face{{font-family:'{fam}';font-style:{st};font-weight:{w};"
                   f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    return "\n".join(css)


def esc(s):
    return html.escape(str(s))


def table(df, num_cols=(), cls=""):
    head = "".join(f'<th class="{"num" if c in num_cols else ""}">{esc(c)}</th>' for c in df.columns)
    body = "".join("<tr>" + "".join(f'<td class="{"num" if c in num_cols else ""}">{esc(v)}</td>'
                                    for c, v in zip(df.columns, row)) + "</tr>" for row in df.itertuples(index=False))
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def code(path, start=None, end=None):
    text = (ROOT / path).read_text()
    if start:
        text = text[text.index(start):]
    if end:
        text = text[:text.index(end)]
    return f'<div class="code-title">{esc(path)}</div><pre class="code">{esc(text.rstrip())}</pre>'


FMT = {"usd": lambda v: f"${v:,.0f}", "pct": lambda v: f"{v * 100:.1f}%", "x": lambda v: f"{v:.2f}x",
       "count": lambda v: f"{v:,.0f}", "months": lambda v: f"{v:.1f} mo", "days": lambda v: f"{v:.1f} d",
       "hours": lambda v: f"{v:.1f} h" if v >= 1 else f"{v * 60:.0f} min", "seconds": lambda v: f"{v * 1000:.1f} ms",
       "usd_per_day": lambda v: f"${v:,.0f}/day", "years": lambda v: f"{v:.1f} yrs"}


def fmt(v, unit):
    return "–" if pd.isna(v) else FMT[unit](v)


def dashboard_screenshot():
    """Render the dashboard with fonts inlined and return a PNG data URI (None if Chromium is missing)."""
    src = (ROOT / "dashboard" / "index.html").read_text()
    src = re.sub(r'<link rel="(preconnect|stylesheet)"[^>]*>', "", src)
    page = HERE / "_dash_shot.html"
    page.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{font_css()}</style></head><body>{src}</body></html>')
    png = HERE / "_dash_shot.png"
    try:
        subprocess.run([CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        "--window-size=1280,860", "--virtual-time-budget=4000", f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True, timeout=120)
        return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        page.unlink(missing_ok=True)
        png.unlink(missing_ok=True)


def example_config(cfg):
    """Compact example of a company config: one line per pipeline/territory, demo-data block dropped."""
    lines = ["{"]
    for k in ("slug", "name", "company_type", "gtm_motion", "slack_channel", "unassigned_owner"):
        lines.append(f'  "{k}": {json.dumps(cfg[k])},')
    lines.append(f'  "economics": {json.dumps(cfg["economics"])},')
    lines.append('  "pipelines": {')
    lines += [f'    {json.dumps(n)}: {json.dumps(r)},' for n, r in cfg["pipelines"].items()]
    lines.append("  },")
    lines.append('  "territories": {')
    lines += [f'    {json.dumps(t)}: {json.dumps(r)},' for t, r in cfg["territories"].items()]
    lines.append("  },")
    lines.append(f'  "icp": {json.dumps(cfg["icp"])}')
    lines.append("}")
    return "\n".join(lines)


def main():
    companies, standard = load_companies(), load_standard()
    names = [c["name"] for c in companies]
    sc = pd.read_csv(OUT / "scorecard.csv")
    ch = pd.read_csv(OUT / "channel_performance.csv")
    ai = pd.read_csv(OUT / "ai_automation_impact.csv")
    calls = pd.read_csv(OUT / "call_tracking_impact.csv")
    info = json.loads((OUT / "run_info.json").read_text())

    def m(co, metric):
        r = sc[(sc.portfolio_company == co) & (sc.metric == metric)].iloc[0]
        return r.value, r.unit

    # ---- scorecard table ----
    rows = []
    for metric_name in sc.metric.drop_duplicates():
        sub = sc[sc.metric == metric_name]
        r0 = sub.iloc[0]
        row = {"Metric": metric_name}
        for co in names:
            r = sub[sub.portfolio_company == co]
            row[co] = fmt(r.value.iloc[0], r0.unit) if len(r) else "–"
        row["Target"] = fmt(r0.target, r0.unit) if not pd.isna(r0.target) else ""
        row["Definition"] = r0.definition
        rows.append(row)
    scorecard_tbl = table(pd.DataFrame(rows), num_cols=names + ["Target"], cls="score")

    # ---- findings (computed, so they stay true when data changes) ----
    findings = []
    for co in names:
        c = ch[ch.portfolio_company == co].dropna(subset=["cac"])
        best, worst = c.sort_values("cac").iloc[0], c.sort_values("cac").iloc[-1]
        findings.append(f"<b>{esc(co)}:</b> {esc(best.channel)} is the cheapest channel at {fmt(best.cac, 'usd')} CAC "
                        f"({fmt(best.ltv_to_cac, 'x')} LTV:CAC). {esc(worst.channel)} costs {fmt(worst.cac, 'usd')} per customer "
                        f"and takes {fmt(worst.spend_share, 'pct')} of spend for {fmt(worst.revenue_share, 'pct')} of bookings.")
    stl = {co: m(co, "Speed to lead (median)")[0] for co in names}
    findings.append("Median speed to lead is " + " and ".join(f"{fmt(v, 'hours')} at {esc(co)}" for co, v in stl.items()) +
                    f" against a {standard['agent']['sla_minutes']}-minute SLA. The AI agent routes a lead and creates the SLA task in milliseconds.")
    mc = {co: (m(co, "Missed call rate")[0], m(co, "Call-influenced bookings")[0]) for co in names}
    worst_call = max(mc, key=lambda k: mc[k][0])
    findings.append(f"At {esc(worst_call)}, {fmt(mc[worst_call][1], 'pct')} of bookings came from buyers who phoned in, "
                    f"yet {fmt(mc[worst_call][0], 'pct')} of tracked calls were missed. Answering the phone is the cheapest growth lever in the portfolio.")
    dc = {co: m(co, "Data completeness")[0] for co in names}
    findings.append("Only " + " and ".join(f"{fmt(v, 'pct')} of records at {esc(co)}" for co, v in dc.items()) +
                    " carry every required field. Completeness is on the scorecard so it gets managed like any other KPI.")
    findings_html = "".join(f"<li>{f}</li>" for f in findings)

    hero = "".join(
        f'<div class="hero-co"><div class="eyebrow">{esc(c["name"])} · {esc(c["gtm_motion"])}</div><div class="hero-row">' +
        "".join(f'<div><span class="v">{fmt(*m(c["name"], k))}</span><span class="l">{esc(lbl)}</span></div>'
                for k, lbl in [("Blended CAC", "Blended CAC"), ("LTV : CAC", "LTV : CAC"),
                               ("CAC payback", "CAC payback"), ("ROAS", "ROAS")]) + "</div></div>"
        for c in companies)

    # ---- HubSpot tables ----
    props = pd.DataFrame([{"Label": p["label"], "Internal name": p["name"], "Objects": ", ".join(p["objects"]),
                           "Type": p["fieldType"], "Options / notes": "; ".join(o["label"] for o in p.get("options", [])) or p["description"]}
                          for p in property_specs(companies, standard)])
    pipes = pd.DataFrame([{"Company": c["name"], "Pipeline": name,
                           "Routing rule": ("Lead source: " + ", ".join(rule["lead_sources"])) if "lead_sources" in rule
                           else f"Service type: {rule['service_type']}"}
                          for c in companies for name, rule in pipelines(c).items()])
    terr = pd.DataFrame([{"Company": c["name"], "Territory": t, "States": ", ".join(s["states"]), "Owner": s["owner"]}
                         for c in companies for t, s in c["territories"].items()])

    # ---- metrics + agent ----
    metric_defs = sc.drop_duplicates("metric")[["category", "metric", "definition"]].rename(
        columns={"category": "Category", "metric": "Metric", "definition": "Standard definition"})
    weights = standard["agent"]["fit_weights"]
    sq = standard["agent"]["source_quality"]
    ai_rows = []
    for r in ai.itertuples():
        ai_rows.append({"Company": r.portfolio_company, "Leads processed (30 d)": f"{r.leads_processed:,}",
                        "Steps automated": f"{r.steps_automated:,}", "Rep hours saved": f"{r.hours_saved:,.0f}",
                        "Speed to lead before": fmt(r.baseline_speed_to_lead_hours, "hours"),
                        "With agent": f"{r.sla_minutes} min SLA", "Hot leads": f"{r.hot_leads:,}"})
    ai_tbl = table(pd.DataFrame(ai_rows), num_cols=["Leads processed (30 d)", "Steps automated", "Rep hours saved", "Hot leads"])
    steps = pd.DataFrame([{"Step": k.replace("_", " ").capitalize(), "Manual minutes replaced": v}
                          for k, v in standard["agent"]["manual_minutes_per_step"].items()])

    shot = dashboard_screenshot()
    shot_html = f'<img class="shot" src="{shot}" alt="Executive dashboard, portfolio view">' if shot else ""

    tpl = (HERE / "template.html").read_text()
    repl = {
        "font_css": font_css(), "date": info["generated_at"][:10], "data_through": info["data_through"],
        "hero": hero, "findings": findings_html, "scorecard_table": scorecard_tbl,
        "properties_table": table(props), "pipelines_table": table(pipes), "territory_table": table(terr),
        "stages": " → ".join(standard["deal_stages"]),
        "metric_defs": table(metric_defs), "ai_table": ai_tbl, "steps_table": table(steps, num_cols=["Manual minutes replaced"]),
        "weights": ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in weights.items()),
        "source_quality": ", ".join(f"{k} {v}" for k, v in sq.items()),
        "hot": str(standard["agent"]["hot_lead_threshold"]), "sla": str(standard["agent"]["sla_minutes"]),
        "sql_funnel": code("sql/channel_funnel.sql"), "sql_attr": code("sql/attribution.sql"),
        "code_agent": code("gtm/agent.py"), "code_scoring": code("gtm/scoring.py"),
        "code_economics": code("gtm/model.py", start="        cac = safe_div(spend_total", end="        C = calls["),
        "code_server": code("gtm/agent_server.py", start="@app.post(\"/webhooks/hubspot\")", end="@app.post(\"/leads\")"),
        "company_json": f'<pre class="code">{esc(example_config(companies[0]))}</pre>',
        "dashboard_shot": shot_html, "company_count": str(len(companies)),
    }
    for k, v in repl.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    missing = re.findall(r"\{\{(\w+)\}\}", tpl)
    assert not missing, f"unfilled placeholders: {missing}"
    src = HERE / "playbook.html"
    src.write_text(tpl)
    pdf = HERE / "GTM_Scorecard_Playbook.pdf"
    subprocess.run([CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=4000", f"--print-to-pdf={pdf}", src.as_uri()],
                   check=True, capture_output=True, timeout=180)
    print(f"Wrote {pdf} ({pdf.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
