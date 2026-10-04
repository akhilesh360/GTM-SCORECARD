"""Rule-based fit score (0-100): weighted sum of employee fit, industry match, served
territory and source quality. Weights live in config/standard.json so every portfolio
company is scored the same way; only the ICP (in the company config) differs."""
import math

from .config import territory_for_state


def employee_fit(employees, lo, hi):
    if lo <= employees <= hi:
        return 1.0
    edge = lo if employees < lo else hi
    distance = abs(math.log(max(employees, 1) / edge))  # log-distance outside the band
    return max(0.0, 1.0 - 0.9 * distance)


def score_components(enriched, lead_source, cfg, standard):
    icp = cfg["icp"]
    sq = standard["agent"]["source_quality"]
    territory, _ = territory_for_state(cfg, enriched["state"])
    return {
        "employees": employee_fit(enriched["employees"], icp["employees_min"], icp["employees_max"]),
        "industry": 1.0 if enriched["industry"] in icp["industries"] else 0.1,
        "territory": 1.0 if territory else 0.0,
        "source_quality": sq.get(lead_source, 0.5),
    }


def score_fit(enriched, lead_source, cfg, standard):
    weights = standard["agent"]["fit_weights"]
    comps = score_components(enriched, lead_source, cfg, standard)
    score = sum(weights[k] * comps[k] for k in weights)
    return int(round(min(100, max(0, score)))), comps
