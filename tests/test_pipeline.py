"""Pipeline checks. Run: python -m pytest tests  (or python -m tests.test_pipeline)."""
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from gtm import clean, generate
from gtm.config import load_companies, load_standard


def _roundtrip(cfg, standard):
    truth = generate.generate_company(cfg, standard)
    raw = generate.messify(truth, cfg, standard)
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / cfg["slug"] / "raw").mkdir(parents=True)
        for t, df in raw.items():
            generate._fmt(df).to_csv(tmp / cfg["slug"] / "raw" / f"{t}.csv", index=False)
        orig = clean.DATA_DIR
        clean.DATA_DIR = tmp
        try:
            cleaned, log = clean.clean_company(cfg, standard)
        finally:
            clean.DATA_DIR = orig
    finally:
        shutil.rmtree(tmp)
    return truth, cleaned, log


def test_cleaning_recovers_truth():
    standard = load_standard()
    for cfg in load_companies():
        truth, cleaned, log = _roundtrip(cfg, standard)
        L, Lt = cleaned["leads"].set_index("lead_id"), truth["leads"].set_index("lead_id")
        assert len(L) == len(Lt), "duplicates not removed"
        for col in ("lead_source", "email", "utm_campaign", "territory"):
            a, b = L[col].fillna("").astype(str), Lt.loc[L.index, col].fillna("").astype(str)
            assert (a == b).all(), f"{cfg['slug']} leads.{col}: {(a != b).sum()} mismatches"
        assert (L.created_date.dt.floor("s") == pd.to_datetime(Lt.loc[L.index, "created_date"]).dt.floor("s")).all()
        D, Dt = cleaned["deals"].set_index("deal_id"), truth["deals"].set_index("deal_id")
        for col in ("lead_source", "stage", "stage_reached", "amount"):
            assert (D[col].astype(str) == Dt[col].astype(str)).all(), f"{cfg['slug']} deals.{col}"
        gm = (D.gross_margin - Dt.gross_margin).abs()
        assert ((gm < 0.0011) | (D.gross_margin.isna() & Dt.gross_margin.isna())).all(), "gross margin"
        S = cleaned["spend"].set_index(["channel", "month"]).spend
        St = truth["spend"].set_index(["channel", "month"]).spend
        assert (S.sort_index() - St.sort_index()).abs().max() < 0.01, "spend"
        assert not any(r["issue"].startswith("Unknown") for r in log), "unmapped channel names"


def test_attribution_models_conserve_bookings():
    out = pd.read_csv(Path(__file__).resolve().parent.parent / "output" / "attribution.csv")
    for _, g in out.groupby("portfolio_company"):
        tot = g.first_touch_revenue.sum()
        assert abs(g.last_touch_revenue.sum() - tot) < 1 and abs(g.linear_revenue.sum() - tot) < 1


def test_agent_falls_back_without_llm(tmp_path=None):
    from gtm.agent import LeadAgent
    from gtm.mocks import MockHubSpot, MockSlack
    tmp = Path(tempfile.mkdtemp())
    agent = LeadAgent(MockHubSpot(tmp), MockSlack(tmp / "s.jsonl"), use_llm=False)
    r = agent.process_lead({"lead_id": "T1", "company": "Example Dental Group", "lead_source": "Referral",
                            "company_type": "HVAC", "state": "TX"})
    assert r["score_source"] == "rules" and r["territory"] == "TX" and r["pipeline"].startswith("Summit HVAC")
    assert 0 <= r["fit_score"] <= 100 and r["task_id"] and r["deal_id"]
    shutil.rmtree(tmp)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
