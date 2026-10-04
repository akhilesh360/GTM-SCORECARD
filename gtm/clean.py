"""Cleaning and normalization layer: data/<slug>/raw/*.csv -> data/<slug>/*.csv.

Every rule is driven by config/standard.json (channel_aliases, stage_aliases, state_names) so the
same code normalizes any portfolio company's exports onto the standard. Each fix is counted in
output/cleaning_log.csv, which the dashboard and playbook show.

  python -m gtm.clean
"""
import re

import numpy as np
import pandas as pd

from .config import DATA_DIR, OUTPUT_DIR, load_companies, load_standard

TABLES = ["leads", "deals", "spend", "activities", "call_tracking", "touchpoints", "deal_stage_history"]
DATE_COLS = {"leads": ["created_date"], "deals": ["created_date", "close_date"], "activities": ["date"],
             "call_tracking": ["call_datetime"], "touchpoints": ["touch_datetime"],
             "deal_stage_history": ["entered_at", "exited_at"]}


class Log:
    def __init__(self, company):
        self.company, self.rows = company, []

    def add(self, table, field, issue, n, example=""):
        if n:
            self.rows.append({"portfolio_company": self.company, "table": table, "field": field,
                              "issue": issue, "rows_fixed": int(n), "example": str(example)[:60]})


def _blank(s):
    return s.isna() | (s.astype(str).str.strip() == "")


def normalize_channel(series, aliases, log, table, field):
    canon = set(v for k, v in aliases.items() if not k.startswith("_"))
    lookup = {k: v for k, v in aliases.items() if not k.startswith("_")}
    lookup.update({c.lower(): c for c in canon})
    key = series.astype(str).str.strip().str.lower()
    out = key.map(lookup)
    changed = (series != out) & out.notna()
    log.add(table, field, "Source name mapped to standard channel", changed.sum(),
            series[changed].iloc[0] if changed.any() else "")
    unmapped = out.isna() & ~_blank(series)
    log.add(table, field, "Unknown source name (set to Unmapped; add an alias)", unmapped.sum(),
            series[unmapped].iloc[0] if unmapped.any() else "")
    return out.fillna("Unmapped").where(~_blank(series), "")


def parse_money(series, log, table, field):
    is_text = series.astype(str).str.contains(r"[$,]", regex=True)
    log.add(table, field, "Currency text parsed to number", is_text.sum(), series[is_text].iloc[0] if is_text.any() else "")
    return pd.to_numeric(series.astype(str).str.replace(r"[$,\s]", "", regex=True), errors="coerce")


def parse_dates(df, table, log):
    for c in DATE_COLS.get(table, []):
        if c not in df:
            continue
        raw = df[c]
        nonstd = raw.notna() & ~raw.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}")
        log.add(table, c, "Non-ISO date format parsed", nonstd.sum(), raw[nonstd].iloc[0] if nonstd.any() else "")
        df[c] = pd.to_datetime(raw, format="mixed", errors="coerce")
    return df


def clean_company(cfg, standard):
    src = DATA_DIR / cfg["slug"] / "raw"
    if not src.exists():
        return None, []
    log = Log(cfg["name"])
    aliases = standard["channel_aliases"]
    f = {t: pd.read_csv(src / f"{t}.csv", dtype=str, keep_default_na=False, na_values=[""])
         for t in TABLES if (src / f"{t}.csv").exists()}
    for t in f:
        f[t] = parse_dates(f[t], t, log)

    # ---- leads ----
    L = f["leads"]
    L["lead_source"] = normalize_channel(L.lead_source, aliases, log, "leads", "lead_source")
    em = L.email.str.strip().str.lower()
    log.add("leads", "email", "Email trimmed / lower-cased", (em != L.email).sum(), L.email[em != L.email].iloc[0] if (em != L.email).any() else "")
    L["email"] = em
    for col in ("utm_source", "utm_medium", "utm_campaign"):
        v = L[col].fillna("").str.strip().str.lower().str.replace(r"\s+", "_", regex=True)
        changed = (v != L[col].fillna(""))
        log.add("leads", col, "UTM value trimmed / lower-cased / spaces to underscores", changed.sum(),
                L[col][changed].iloc[0] if changed.any() else "")
        L[col] = v.replace("", np.nan)
    states = standard["state_names"]
    valid = set(cfg["territories"])
    t_raw = L.territory.fillna("").str.strip()
    t = t_raw.map(lambda x: states.get(x.lower(), x.upper()) if x else "")
    t = t.where(t.isin(valid | {"Unassigned"}) | (t == ""), "Unassigned")
    changed = (t != t_raw) & (t != "")
    log.add("leads", "territory", "Territory normalized (case / state name)", changed.sum(), t_raw[changed].iloc[0] if changed.any() else "")
    L["territory"] = t.replace("", np.nan)
    L["fit_score"] = pd.to_numeric(L.fit_score, errors="coerce").astype("Int64")
    before = len(L)
    L = L.sort_values("created_date").drop_duplicates("email", keep="first").sort_values("lead_id")
    log.add("leads", "email", "Duplicate lead removed (same email, later record)", before - len(L))
    f["leads"] = L.reset_index(drop=True)

    # ---- deals ----
    D = f["deals"]
    D["lead_source"] = normalize_channel(D.lead_source, aliases, log, "deals", "lead_source")
    stage_map = {**{k: v for k, v in standard["stage_aliases"].items()}, **{s.lower(): s for s in standard["deal_stages"]}}
    for col in ("stage", "stage_reached"):
        st = D[col].str.strip().str.lower().map(stage_map)
        changed = st != D[col]
        log.add("deals", col, "Stage label mapped to standard stage", changed.sum(), D[col][changed].iloc[0] if changed.any() else "")
        D[col] = st
    D["amount"] = parse_money(D.amount, log, "deals", "amount")
    gm_txt = D.gross_margin.fillna("")
    pct = gm_txt.str.endswith("%")
    gm = pd.to_numeric(gm_txt.str.rstrip("%"), errors="coerce")
    scaled = pct | (gm > 1.5)
    log.add("deals", "gross_margin", "Percent-style margin converted to fraction", scaled.sum(), gm_txt[scaled].iloc[0] if scaled.any() else "")
    D["gross_margin"] = np.where(scaled, gm / 100, gm).round(3)
    f["deals"] = D

    # ---- spend ----
    S = f["spend"]
    S["channel"] = normalize_channel(S.channel, aliases, log, "spend", "channel")
    S["spend"] = parse_money(S.spend, log, "spend", "spend")
    for c in ("impressions", "clicks"):
        S[c] = pd.to_numeric(S[c], errors="coerce").astype("Int64")
    f["spend"] = S.groupby(["channel", "month"], as_index=False).agg(
        spend=("spend", "sum"), impressions=("impressions", "sum"), clicks=("clicks", "sum"))

    # ---- other tables ----
    for t in ("touchpoints", "call_tracking"):
        f[t]["channel"] = normalize_channel(f[t].channel, aliases, log, t, "channel")
    A = f["activities"]
    ty = A.type.str.strip().str.lower()
    log.add("activities", "type", "Activity type lower-cased", (ty != A.type).sum())
    A["type"] = ty
    C = f["call_tracking"]
    for c in ("duration", "revenue_influenced"):
        C[c] = pd.to_numeric(C[c], errors="coerce")
    return f, log.rows


def _fmt(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


def main():
    standard = load_standard()
    rows = []
    for cfg in load_companies():
        frames, log = clean_company(cfg, standard)
        if frames is None:
            print(f"{cfg['name']}: no raw/ folder, using data/{cfg['slug']}/*.csv as already clean")
            continue
        for t, df in frames.items():
            _fmt(df).to_csv(DATA_DIR / cfg["slug"] / f"{t}.csv", index=False)
        rows += log
        print(f"{cfg['name']}: {sum(r['rows_fixed'] for r in log):,} fixes across {len(log)} rules")
    OUTPUT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows, columns=["portfolio_company", "table", "field", "issue", "rows_fixed", "example"]) \
        .to_csv(OUTPUT_DIR / "cleaning_log.csv", index=False)


if __name__ == "__main__":
    main()
