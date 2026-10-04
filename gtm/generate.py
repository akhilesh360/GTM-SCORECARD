"""Synthetic GTM data generator.

Produces, per portfolio company (data/<slug>/):
  leads.csv, deals.csv, spend.csv, activities.csv, call_tracking.csv   (schemas from the design doc)
  touchpoints.csv          multi-touch journeys, needed for first/last/linear attribution
  deal_stage_history.csv   stage entry/exit timestamps, needed for days-in-stage and velocity

All behaviour is driven by the company's "synthetic" block in config/companies/<slug>.json,
so a third archetype is a new JSON file, not new code. For a real company, skip this step
and export the same CSV schemas from its CRM, ad platforms and call-tracking tool.
"""
import math
import random
from datetime import datetime, timedelta

import pandas as pd
from faker import Faker

from .config import DATA_DIR, load_companies, load_standard, pipeline_for, territory_for_state
from .enrich import company_domain, mock_enrich
from .scoring import score_fit

START = datetime(2026, 4, 1)
MONTHS = 6
END = datetime(2026, 9, 30, 23, 59, 59)

TITLES = {"generic": ["Owner", "Operations Manager", "General Manager", "Office Manager", "Director of Operations"]}
CALL_SOURCES = {
    "Call Tracking": ["Google Business Profile", "Local Services Ads"],
    "Google Ads": ["Google Ads Call Extension"],
    "Meta Ads": ["Meta Click-to-Call"],
    "Referral": ["Referral Line"],
    "Outbound": ["Outbound Callback Number"],
}
CALL_PROPENSITY = {"Google Ads": 1.2, "Meta Ads": 0.6, "Outbound": 0.5, "Referral": 0.8}
UTM = {"Google Ads": ("google", "cpc"), "Meta Ads": ("facebook", "paid_social"), "Call Tracking": ("callrail", "phone")}


def month_start(i):
    y, m = START.year, START.month + i
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return datetime(y, m, 1)


def gamma_days(rng, mean, shape=2.0):
    return rng.gammavariate(shape, mean / shape) if mean > 0 else 0.0


def weighted_mean(values, weights):
    return sum(values[k] * weights[k] for k in weights) / sum(weights.values())


def pick(rng, dist):
    return rng.choices(list(dist), weights=list(dist.values()))[0]


def lognormal_mean(rng, mean, sigma):
    return rng.lognormvariate(math.log(mean) - sigma ** 2 / 2, sigma)


def random_created(rng, ms, me, after_hours_share):
    while True:
        dt = ms + timedelta(seconds=rng.uniform(0, (me - ms).total_seconds()))
        business = 8 <= dt.hour < 18 and dt.weekday() < 5
        # e.g. HVAC emergencies come in around the clock; MSP leads cluster in business hours
        if business or rng.random() < after_hours_share:
            return dt


def generate_company(cfg, standard):
    syn = cfg["synthetic"]
    rng = random.Random(syn["seed"])
    fake = Faker("en_US")
    fake.seed_instance(syn["seed"])
    prefix = cfg["slug"].split("_")[0].upper()[:4]
    ctype = cfg["company_type"]
    channels = list(syn["channel_mix"])

    # ---------- leads ----------
    seas = syn["seasonality"]
    seas_mean = sum(seas) / len(seas)
    leads = []
    for mi in range(MONTHS):
        ms, me = month_start(mi), month_start(mi + 1)
        n = round(syn["leads_per_month"] * seas[mi] / seas_mean * rng.uniform(0.97, 1.03))
        for _ in range(n):
            created = random_created(rng, ms, min(me, END), syn.get("after_hours_share", 0.2))
            ch = pick(rng, syn["channel_mix"])
            company = fake.company().replace(",", "")
            enr = mock_enrich(company, cfg)
            territory, owner = territory_for_state(cfg, enr["state"])
            first, last = fake.first_name(), fake.last_name()
            fit, _ = score_fit(enr, ch, cfg, standard)
            quarter = "q2" if created.month <= 6 else "q3"
            utm_source, utm_medium, utm_campaign = "", "", ""
            if ch in ("Google Ads", "Meta Ads"):
                utm_source, utm_medium = UTM[ch]
                camps = [c for c in syn["campaigns"][ch] if f"_{quarter}_" in c] or syn["campaigns"][ch]
                utm_campaign = rng.choice(camps)
            elif ch == "Call Tracking":
                utm_source, utm_medium = UTM[ch]
                utm_campaign = rng.choice(["gbp_listing", "local_services"])
            leads.append({
                "lead_id": None, "company": company, "contact_name": f"{first} {last}",
                "title": rng.choice(syn.get("titles") or TITLES["generic"]),
                "email": f"{first.lower()}.{last.lower()}@{company_domain(company)}",
                "created_date": created, "lead_source": ch,
                "utm_source": utm_source, "utm_medium": utm_medium, "utm_campaign": utm_campaign,
                "territory": territory or "Unassigned", "fit_score": fit,
                "_owner": owner, "_state": enr["state"],
            })
    leads.sort(key=lambda r: r["created_date"])
    for i, r in enumerate(leads, 1):
        r["lead_id"] = f"{prefix}-L{i:05d}"

    # ---------- conversion probabilities (channel rate x fit lift, renormalised per channel) ----------
    mix = syn["channel_mix"]
    opp_norm = weighted_mean(syn["opp_multiplier"], mix)
    p_opp_ch = {c: syn["lead_to_opp"] * syn["opp_multiplier"][c] / opp_norm for c in channels}
    exp_opps = {c: mix[c] * p_opp_ch[c] for c in channels}
    win_norm = weighted_mean(syn["win_multiplier"], exp_opps)
    p_win_ch = {c: syn["opp_to_won"] * syn["win_multiplier"][c] / win_norm for c in channels}

    def fit_lift(f):
        return 0.35 + 1.3 * f / 100

    lift_mean = {c: sum(fit_lift(r["fit_score"]) for r in leads if r["lead_source"] == c) /
                 max(1, sum(1 for r in leads if r["lead_source"] == c)) for c in channels}

    def win_lift(f):
        return 0.6 + 0.8 * f / 100

    # mean win lift among expected opportunities, so the fit effect does not shift the overall win rate
    win_lift_mean = sum(fit_lift(r["fit_score"]) * win_lift(r["fit_score"]) for r in leads) / \
        sum(fit_lift(r["fit_score"]) for r in leads)

    # ---------- deals, stage history ----------
    deals, history = [], []
    order = ["New", "Contacted", "Qualified", "Proposal"]
    for r in leads:
        ch = r["lead_source"]
        p_opp = min(0.95, p_opp_ch[ch] * fit_lift(r["fit_score"]) / lift_mean[ch])
        will_opp = rng.random() < p_opp
        p_deal_only = max(0.0, min(1.0, (p_opp / syn["opp_per_deal"] - p_opp) / max(1e-9, 1 - p_opp)))
        r["_opp_intent"] = will_opp
        if not will_opp and rng.random() >= p_deal_only:
            continue
        will_win = will_opp and rng.random() < min(0.95, p_win_ch[ch] * win_lift(r["fit_score"]) / win_lift_mean)
        service = pick(rng, syn["service_mix_by_channel"][ch]) if "service_mix_by_channel" in syn else None
        pipe = pipeline_for(cfg, ch, service)
        sd = syn["stage_days_by_pipeline"][pipe] if "stage_days_by_pipeline" in syn else syn["stage_days"]

        if will_win:
            path = order + ["Won"]
        elif will_opp:
            path = order[:3] + (["Proposal"] if rng.random() < 0.6 else []) + ["Lost"]
        else:
            path = ["New"] + (["Contacted"] if rng.random() < 0.8 else []) + ["Lost"]

        deal_id = f"{prefix}-D{len(deals) + 1:05d}"
        t = r["created_date"] + timedelta(days=gamma_days(rng, sd["lead_to_deal"]))
        if t > END:
            continue
        created = t
        current, closed_at = path[0], None
        for stage in path:
            if stage in ("Won", "Lost"):
                current, closed_at = stage, t
                history.append({"deal_id": deal_id, "stage": stage, "entered_at": t, "exited_at": None})
                break
            dur = gamma_days(rng, sd[stage])
            exit_t = t + timedelta(days=dur)
            if exit_t > END:  # deal still sitting in this stage at the data cut-off
                current = stage
                history.append({"deal_id": deal_id, "stage": stage, "entered_at": t, "exited_at": None})
                break
            history.append({"deal_id": deal_id, "stage": stage, "entered_at": t, "exited_at": exit_t})
            t = exit_t
        entered = [h["stage"] for h in history if h["deal_id"] == deal_id]
        reached = "Won" if "Won" in entered else max((s for s in entered if s in order), key=order.index)
        amount = round(lognormal_mean(rng, syn["amount_by_pipeline"][pipe], 0.35) / 50) * 50
        margin = min(0.85, max(0.15, rng.gauss(syn["margin_by_pipeline"][pipe], 0.04)))
        deals.append({
            "deal_id": deal_id, "lead_id": r["lead_id"], "company": r["company"], "stage": current,
            "amount": amount, "close_date": closed_at.date().isoformat() if closed_at else "",
            "lead_source": ch, "gross_margin": round(margin, 3), "pipeline": pipe,
            "owner": r["_owner"], "created_date": created, "stage_reached": reached,
        })
        r["_deal"] = deals[-1]

    # ---------- activities ----------
    activities = []
    stl_mu = math.log(syn["speed_to_lead_median_hours"])
    for r in leads:
        deal = r.get("_deal")
        n = 1 + _poisson(rng, 1.2) if deal is None else (2 + _poisson(rng, 2)) if not r["_opp_intent"] else 4 + _poisson(rng, 3)
        first = r["created_date"] + timedelta(hours=rng.lognormvariate(stl_mu, 1.0))
        horizon_end = END
        if deal is not None and deal["close_date"]:
            horizon_end = min(END, datetime.fromisoformat(deal["close_date"]) + timedelta(days=1))
        elif deal is None:
            horizon_end = min(END, r["created_date"] + timedelta(days=21))
        if first > END:
            continue
        times = [first] + sorted(first + timedelta(seconds=rng.uniform(0, max(60, (horizon_end - first).total_seconds())))
                                 for _ in range(n - 1))
        for k, ts in enumerate(times):
            if ts > END:
                break
            if k == 0:
                typ = "call" if rng.random() < 0.6 else "email"
            else:
                typ = pick(rng, {"call": 0.45, "email": 0.43, "meeting": 0.12 if deal is not None else 0.0})
            outcome = {
                "call": lambda: pick(rng, {"connected": 0.35, "no_answer": 0.4, "voicemail": 0.25}),
                "email": lambda: pick(rng, {"sent": 0.75, "replied": 0.2, "bounced": 0.05}),
                "meeting": lambda: pick(rng, {"held": 0.75, "no_show": 0.12, "rescheduled": 0.13}),
            }[typ]()
            activities.append({"activity_id": None, "lead_id": r["lead_id"], "type": typ, "date": ts,
                               "outcome": outcome, "owner": r["_owner"]})
    activities.sort(key=lambda a: a["date"])
    for i, a in enumerate(activities, 1):
        a["activity_id"] = f"{prefix}-A{i:06d}"

    # ---------- call tracking ----------
    calls = []
    for r in leads:
        ch = r["lead_source"]
        deal = r.get("_deal")
        conv_end = r["created_date"] + timedelta(days=30)
        if deal is not None and deal["close_date"]:
            conv_end = datetime.fromisoformat(deal["close_date"]) + timedelta(hours=12)
        times = []
        if ch == "Call Tracking":
            times.append((r["created_date"], rng.choice(CALL_SOURCES["Call Tracking"]), "Call Tracking"))
        if rng.random() < syn["call_share"] * CALL_PROPENSITY.get(ch, 0.6):
            for _ in range(1 + _poisson(rng, 0.4)):
                cch = ch if (ch != "Call Tracking" and rng.random() < 0.5) else "Call Tracking"
                ts = r["created_date"] + timedelta(seconds=rng.uniform(0, (conv_end - r["created_date"]).total_seconds()))
                times.append((ts, rng.choice(CALL_SOURCES[cch]), cch))
        for ts, src, cch in times:
            if ts > END:
                continue
            miss_p = syn["missed_call_rate"] * (0.4 if r["_opp_intent"] else 1.3)
            u = rng.random()
            if u < miss_p:
                outcome, dur = "missed", 0
            elif u < miss_p + 0.06:
                outcome, dur = "voicemail", rng.randint(20, 75)
            else:
                booked = r["_opp_intent"] and rng.random() < 0.7
                outcome = "answered_booked" if booked else "answered_info"
                dur = int(rng.uniform(180, 900) if booked else rng.uniform(45, 420))
            rev = 0
            if deal is not None and deal["stage"] == "Won" and outcome != "missed" and \
                    ts.date().isoformat() <= deal["close_date"]:
                rev = deal["amount"]
            calls.append({"call_id": None, "lead_id": r["lead_id"], "source": src, "duration": dur,
                          "outcome": outcome, "revenue_influenced": rev, "call_datetime": ts, "channel": cch})
    calls.sort(key=lambda c: c["call_datetime"])
    for i, c in enumerate(calls, 1):
        c["call_id"] = f"{prefix}-C{i:05d}"

    # ---------- touchpoints (multi-touch journeys) ----------
    touches = []
    calls_by_lead = {}
    for c in calls:
        calls_by_lead.setdefault(c["lead_id"], []).append(c)
    for r in leads:
        ch = r["lead_source"]
        lag = {"Outbound": (3, 20), "Referral": (0, 5), "Call Tracking": (0, 0)}.get(ch, (0, 10))
        first_t = r["created_date"] - timedelta(days=rng.uniform(*lag))
        deal = r.get("_deal")
        conv = deal["created_date"] if deal is not None else min(END, r["created_date"] + timedelta(days=30))
        tlist = [(first_t, ch, r["utm_campaign"], "first_touch")]
        for _ in range(_poisson(rng, syn["extra_touches_mean"])):
            tch = pick(rng, syn["touch_mix"])
            ts = first_t + timedelta(seconds=rng.uniform(1, max(2, (conv - first_t).total_seconds())))
            camp = rng.choice(syn["campaigns"][tch]) if tch in syn["campaigns"] else ""
            tlist.append((ts, tch, camp, "touch"))
        for c in calls_by_lead.get(r["lead_id"], []):
            if c["channel"] == "Call Tracking" and c["call_datetime"] > first_t + timedelta(seconds=1):
                tlist.append((c["call_datetime"], "Call Tracking", "", "call"))
        for ts, tch, camp, ttype in sorted(tlist, key=lambda x: x[0]):
            if ts <= END:
                touches.append({"touch_id": None, "lead_id": r["lead_id"], "touch_datetime": ts,
                                "channel": tch, "utm_campaign": camp, "touch_type": ttype})
    for i, t in enumerate(touches, 1):
        t["touch_id"] = f"{prefix}-T{i:06d}"

    # ---------- spend ----------
    spend = []
    for mi in range(MONTHS):
        mlabel = month_start(mi).strftime("%Y-%m")
        sf = 0.85 + 0.15 * seas[mi] / seas_mean
        for ch, budget in syn["monthly_budget"].items():
            s = round(budget * sf * rng.uniform(0.94, 1.06), 2)
            if ch == "Google Ads":
                clicks = int(s / syn.get("google_cpc", 7.0))
                impr = int(clicks / rng.uniform(0.035, 0.05))
            elif ch == "Meta Ads":
                impr = int(s / 24 * 1000)
                clicks = int(impr * rng.uniform(0.008, 0.012))
            elif ch == "Outbound":
                impr = int(s / 7.5)              # emails + dials sent
                clicks = int(impr * rng.uniform(0.02, 0.03))  # positive replies / connects
            elif ch == "Call Tracking":
                impr = int(s / 0.6)              # GBP / LSA profile views
                clicks = int(impr * rng.uniform(0.025, 0.035))
            else:
                impr, clicks = None, None        # referral payouts have no impressions
            spend.append({"channel": ch, "month": mlabel, "spend": s, "impressions": impr, "clicks": clicks})

    # ---------- data-quality gaps (deliberate, so completeness is measurable) ----------
    miss = syn["missing_rates"]
    for r in leads:
        if rng.random() < miss["title"]:
            r["title"] = ""
        if rng.random() < miss["territory"]:
            r["territory"] = ""
        if r["lead_source"] in standard["paid_ad_channels"] and rng.random() < miss["utm_campaign"]:
            r["utm_campaign"] = ""
        if rng.random() < miss["fit_score"]:
            r["fit_score"] = None
    for d in deals:
        if rng.random() < miss["gross_margin"]:
            d["gross_margin"] = None

    lead_cols = ["lead_id", "company", "contact_name", "title", "email", "created_date", "lead_source",
                 "utm_source", "utm_medium", "utm_campaign", "territory", "fit_score"]
    frames = {
        "leads": pd.DataFrame(leads)[lead_cols],
        "deals": pd.DataFrame(deals),
        "spend": pd.DataFrame(spend),
        "activities": pd.DataFrame(activities),
        "call_tracking": pd.DataFrame(calls)[["call_id", "lead_id", "source", "duration", "outcome",
                                              "revenue_influenced", "call_datetime", "channel"]],
        "touchpoints": pd.DataFrame(touches),
        "deal_stage_history": pd.DataFrame(history),
    }
    frames["leads"]["fit_score"] = frames["leads"]["fit_score"].astype("Int64")
    for col in ("impressions", "clicks"):
        frames["spend"][col] = frames["spend"][col].astype("Int64")
    return frames


def _poisson(rng, lam):
    # Knuth's algorithm; fine for the small lambdas used here
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def _fmt(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


STATE_NAME = {"NY": "New York", "NJ": "New Jersey", "MA": "Massachusetts", "TX": "Texas", "CA": "California",
              "IL": "Illinois", "FL": "Florida", "AZ": "Arizona", "GA": "Georgia", "NC": "North Carolina"}


def messify(frames, cfg, standard):
    """Turn clean frames into realistic raw exports: inconsistent source names, mixed date and
    money formats, percent-style margins, stage label drift, casing/whitespace noise and duplicate
    leads. gtm/clean.py must undo all of it (tests/test_pipeline.py checks the round trip)."""
    syn = cfg["synthetic"]
    rate = syn.get("messy_rate", 0.2)
    rng = random.Random(syn["seed"] + 1000)
    aliases = {}
    for alias, canon in standard["channel_aliases"].items():
        if not alias.startswith("_") and alias != canon.lower():
            aliases.setdefault(canon, []).append(alias)

    def noisy_channel(c):
        if rng.random() >= rate:
            return c
        a = rng.choice(aliases[c])
        return rng.choice([a, a.title(), a.upper(), f" {a} ", a.capitalize()])

    raw = {k: v.copy() for k, v in frames.items()}
    L = raw["leads"]
    L["lead_source"] = L.lead_source.map(noisy_channel)
    L["utm_campaign"] = L.utm_campaign.map(lambda v: (f" {v.upper()}" if rng.random() < 0.5 else v.replace("_", " "))
                                           if isinstance(v, str) and v and rng.random() < rate / 2 else v)
    L["utm_source"] = L.utm_source.map(lambda v: v.title() + " " if isinstance(v, str) and v and rng.random() < rate / 2 else v)
    L["email"] = L.email.map(lambda v: f"{v.upper()} " if rng.random() < rate / 3 else v)
    L["territory"] = L.territory.map(lambda v: (STATE_NAME.get(v, v.lower()) if rng.random() < 0.5 else v.lower())
                                     if isinstance(v, str) and v and rng.random() < rate / 2 else v)
    L["created_date"] = L.created_date.map(lambda d: d.strftime("%m/%d/%Y %H:%M:%S") if rng.random() < rate / 2 else d)
    dupes = L.sample(frac=0.02, random_state=syn["seed"]).copy()
    prefix = cfg["slug"].split("_")[0].upper()[:4]
    dupes["lead_id"] = [f"{prefix}-L9{i:04d}" for i in range(len(dupes))]
    dupes["email"] = dupes.email.str.strip().str.title()
    dupes["created_date"] = [pd.to_datetime(d, format="mixed") + timedelta(minutes=rng.randint(5, 4000)) for d in dupes.created_date]
    raw["leads"] = pd.concat([L, dupes], ignore_index=True)

    D = raw["deals"]
    D["lead_source"] = D.lead_source.map(noisy_channel)
    stage_alias = {"Won": ["Closed Won", "closedwon", "won"], "Lost": ["Closed Lost", "closedlost", "LOST"],
                   "Proposal": ["Proposal Sent", "proposal"], "Qualified": ["SQL", "qualified"], "New": ["new"], "Contacted": ["contacted"]}
    D["stage"] = D.stage.map(lambda s: rng.choice(stage_alias[s]) if rng.random() < rate else s)
    D["amount"] = D.amount.map(lambda a: f"${a:,.0f}" if rng.random() < rate / 2 else a)
    D["gross_margin"] = D.gross_margin.map(lambda g: g if pd.isna(g) else
                                           (f"{g * 100:.1f}%" if (u := rng.random()) < rate / 2 else round(g * 100, 1) if u < rate else g))

    S = raw["spend"]
    export_names = {c: rng.choice(aliases[c]).title() for c in S.channel.unique()}
    S["channel"] = S.channel.map(export_names)          # one consistent "export name" per company
    S["spend"] = S.spend.map(lambda v: f"${v:,.2f}")
    for t, col in (("touchpoints", "channel"), ("call_tracking", "channel")):
        raw[t][col] = raw[t][col].map(noisy_channel)
    A = raw["activities"]
    A["type"] = A.type.map(lambda v: rng.choice([v.title(), v.upper()]) if rng.random() < rate / 2 else v)
    return raw


def main():
    standard = load_standard()
    for cfg in load_companies():
        frames = generate_company(cfg, standard)
        raw = messify(frames, cfg, standard)
        out = DATA_DIR / cfg["slug"] / "raw"
        out.mkdir(parents=True, exist_ok=True)
        for name, df in raw.items():
            _fmt(df).to_csv(out / f"{name}.csv", index=False)
        print(f"{cfg['name']} (raw): " + ", ".join(f"{k}={len(v)}" for k, v in raw.items()))


if __name__ == "__main__":
    main()
