"""Scorecard Q&A: answers questions only from the computed outputs (output/*.csv).

build_context() turns the model outputs into a compact fact sheet. The dashboard embeds it, and
ask() sends it to DeepSeek with RULES and the tools in chat_tools.py, so the chat can also act
(score a lead, add a test lead). The DeepSeek key never leaves the server.

  python -m gtm.chat "Why is Summit's CAC lower?"
"""
import json
import os
import sys
import urllib.error

import pandas as pd

from .config import OUTPUT_DIR
from .integrations import _http

RULES = ("You are the GTM agent for a portfolio scorecard covering two synthetic companies. "
         "Answer questions using ONLY the facts below or your tools, and run a tool when the user asks you to do "
         "something (compare, switch attribution model, score a lead, add a test lead). "
         "Reply in at most 3 short sentences and cite the numbers. "
         "If neither the facts nor a tool can answer, reply exactly: Not in the data. "
         "Never guess or use outside knowledge about these companies. "
         "To score a lead you need its company name, state and lead source; ask for missing ones.")


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


def ask(question, history=(), context=None, max_rounds=4):
    """history: [{"role": "user"|"assistant", "content": str}, ...].
    Returns {"answer": str, "ran": [tool names]}; DeepSeek may call the tools in chat_tools."""
    from . import chat_tools
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set in .env")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    msgs = [{"role": "system", "content": RULES + "\n\n# Facts\n" + (context or build_context())}]
    msgs += [{"role": m["role"], "content": str(m["content"])[:2000]} for m in list(history)[-6:]
             if m.get("role") in ("user", "assistant") and m.get("content")]
    msgs.append({"role": "user", "content": str(question)[:1000]})
    ran = []
    for i in range(max_rounds):
        payload = {"model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"), "messages": msgs,
                   "temperature": 0.1, "max_tokens": 300}
        if i < max_rounds - 1:
            payload["tools"] = chat_tools.TOOLS
        msg = _http("POST", f"{base}/chat/completions", key, payload, timeout=60)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": (msg.get("content") or "").strip() or "Not in the data.", "ran": ran}
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                out = chat_tools.run(name, json.loads(c["function"].get("arguments") or "{}"))
            except Exception as e:  # the model sees the error and can tell the user
                out = {"error": f"{type(e).__name__}: {e}"}
            ran.append(name)
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)[:6000]})
    return {"answer": "I ran out of steps for that request. Try asking for one thing at a time.", "ran": ran}


if __name__ == "__main__":
    try:
        r = ask(" ".join(sys.argv[1:]) or "Which company has the better LTV:CAC?")
        print((f"[ran: {', '.join(r['ran'])}] " if r["ran"] else "") + r["answer"])
    except urllib.error.HTTPError as e:
        print(f"DeepSeek error HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except RuntimeError as e:
        print(e)
