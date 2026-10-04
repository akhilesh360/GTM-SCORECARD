"""Config loading. Everything company-specific lives in config/companies/<slug>.json;
everything portfolio-wide lives in config/standard.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


def load_standard():
    return json.loads((CONFIG_DIR / "standard.json").read_text())


def load_company(slug):
    return json.loads((CONFIG_DIR / "companies" / f"{slug}.json").read_text())


def company_slugs():
    return sorted(p.stem for p in (CONFIG_DIR / "companies").glob("*.json"))


def load_companies():
    return [load_company(s) for s in company_slugs()]


def pipelines(cfg):
    return {k: v for k, v in cfg["pipelines"].items() if not k.startswith("_")}


def territory_for_state(cfg, state):
    """Map a state code to (territory, owner). Unknown states go to the unassigned queue."""
    for terr, spec in cfg["territories"].items():
        if state in spec["states"]:
            return terr, spec["owner"]
    return None, cfg["unassigned_owner"]


def pipeline_for(cfg, lead_source, service_type=None):
    """Pick the deal pipeline using the company's routing rule (by lead source or by service type)."""
    for name, rule in pipelines(cfg).items():
        if "lead_sources" in rule and lead_source in rule["lead_sources"]:
            return name
        if "service_type" in rule and service_type == rule["service_type"]:
            return name
    return next(iter(pipelines(cfg)))
