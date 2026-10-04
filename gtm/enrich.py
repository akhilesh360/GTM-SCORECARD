"""Mock firmographic enrichment. Deterministic per company name so the generator, the
attribution model and the agent all see the same firmographics. In production, swap
mock_enrich() for a real provider (Apollo, Clearbit, ZoomInfo) with the same return shape."""
import hashlib
import math
import random

OTHER_INDUSTRIES = ["Hospitality", "Nonprofit", "Construction", "Software", "Logistics", "Government"]


def _rng(company_name, salt=""):
    seed = int(hashlib.sha256(f"{salt}{company_name}".encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def mock_enrich(company_name, cfg):
    rng = _rng(company_name, cfg["slug"])
    icp = cfg["icp"]
    industries = icp["industries"]
    if rng.random() < 0.45:
        industry = rng.choice(industries)
    else:
        industry = rng.choice([i for i in OTHER_INDUSTRIES + industries if i not in industries] or OTHER_INDUSTRIES)
    median = math.sqrt(icp["employees_min"] * icp["employees_max"]) * 0.8
    employees = max(3, int(rng.lognormvariate(math.log(median), 1.4)))
    states = cfg["synthetic"]["state_mix"]
    state = rng.choices(list(states), weights=list(states.values()))[0]
    revenue = int(employees * rng.uniform(90_000, 220_000))
    return {
        "company": company_name,
        "industry": industry,
        "employees": employees,
        "annual_revenue": revenue,
        "state": state,
        "domain": company_domain(company_name),
        "enrichment_source": "mock",
    }


def company_domain(company_name):
    slug = "".join(ch for ch in company_name.lower() if ch.isalnum())[:24]
    return f"{slug}.example.com"
