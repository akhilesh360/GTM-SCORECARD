"""Scorecard Q&A: answers questions only from the computed outputs (output/*.csv).

build_context() turns the model outputs into a compact fact sheet. The dashboard embeds it, and
ask() sends it to DeepSeek with RULES. The DeepSeek key never leaves the server.

  python -m gtm.chat "Why is Summit's CAC lower?"
"""
import json
import os
import sys
import urllib.error

import pandas as pd

from .config import OUTPUT_DIR
from .integrations import _http

RULES = ("You answer questions about a portfolio GTM scorecard for two synthetic companies. "
         "Use ONLY the facts below. Reply in at most 3 short sentences and cite the numbers. "
         "If the facts don't contain the answer, reply exactly: Not in the data. "
         "Never guess or use outside knowledge about these companies.")


def _csv(name, cols=None, digits=3):
    p = OUTPUT_DIR / f"{name}.csv"
    if not p.exists():
        return ""
    df = pd.read_csv(p)
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return f"## {name}\n" + df.round(digits).to_csv(index=False)


def build_context():
    parts = [
        _csv("scorecard_wide", ["category", "metric", "unit", "definition", "Acme MSP", "Summit HVAC"]),
        _csv("channel_performance", ["portfolio_company", "channel", "total_spend", "leads", "opps", "won", "revenue",
                                     "cac", "roas", "avg_margin", "avg_acv", "lead_to_opp", "opp_to_won",
                                     "ltv_to_cac", "cac_payback_months"]),
        _csv("attribution", ["portfolio_company", "channel", "first_touch_revenue", "last_touch_revenue",
                             "linear_revenue", "total_spend", "first_touch_roas", "last_touch_roas", "linear_roas"]),
        _csv("call_tracking_impact", ["portfolio_company", "source", "calls", "missed", "booked", "missed_rate",
                                      "revenue_influenced"]),
        _csv("stage_days", ["portfolio_company", "pipeline", "stage", "avg_days", "deals"], 1),
        _csv("fit_score_validation"),
        _csv("ai_automation_impact", ["portfolio_company", "leads_processed", "deepseek_scored", "hot_leads",
                                      "hours_saved", "baseline_speed_to_lead_hours", "sla_minutes", "sla_hit_rate",
                                      "baseline_sla_hit_rate", "avg_deepseek_seconds"]),
    ]
    p = OUTPUT_DIR / "run_info.json"
    if p.exists():
        parts.insert(0, "## run_info\n" + p.read_text().strip() + "\nAll data is synthetic. Units: pct and rates are 0-1 fractions, usd in dollars, x = ratio.")
    return "\n".join(x for x in parts if x)


def ask(question, history=(), context=None):
    """history: [{"role": "user"|"assistant", "content": str}, ...]. Returns the answer text."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set in .env")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    msgs = [{"role": "system", "content": RULES + "\n\n# Facts\n" + (context or build_context())}]
    msgs += [{"role": m["role"], "content": str(m["content"])[:2000]} for m in list(history)[-6:]
             if m.get("role") in ("user", "assistant") and m.get("content")]
    msgs.append({"role": "user", "content": str(question)[:1000]})
    res = _http("POST", f"{base}/chat/completions", key, {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"), "messages": msgs,
        "temperature": 0.1, "max_tokens": 250,
    }, timeout=40)
    return res["choices"][0]["message"]["content"].strip()


if __name__ == "__main__":
    try:
        print(ask(" ".join(sys.argv[1:]) or "Which company has the better LTV:CAC?"))
    except urllib.error.HTTPError as e:
        print(f"DeepSeek error HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except RuntimeError as e:
        print(e)
