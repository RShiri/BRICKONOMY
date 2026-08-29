"""Trend-based value forecast with retirement-phase adjustment.

Projection: value * (1 + g_eff)^t where g is the best growth estimate clamped
to sane bounds and g_eff applies a phase multiplier modeled on the well-known
retirement cycle (bump at retirement, 6–24 month acceleration, stabilization).
All tuning constants live in PARAMS. Outputs carry a ±band and are labeled
"trend estimate" in the UI — this is not a statistical model.
"""
from datetime import datetime

from . import growth as growth_mod
from . import lifecycle
from .. import db as dbq
from .valuation import current_value

PARAMS = {
    "clamp_min_pct": -10.0,       # annual growth clamp
    "clamp_max_pct": 25.0,
    "phase_multiplier": {
        "NEW": 0.5,               # flooded market damps the trend
        "EOL WATCH": 1.0,
        "RETIRED_ACCEL": 1.5,     # 0-2y post retirement
        "RETIRED_STABLE": 0.7,
        None: 1.0,
    },
    "retirement_step_pct": 10.0,  # one-time bump applied when the horizon
                                  # crosses the estimated retirement year
    "band_pct": 20.0,
    "horizons_years": (2, 5),
}


def forecast(conn, item_id, condition="new"):
    """Returns {'basis', 'growth_pct', 'phase', 'horizons': {years: {value, low, high}}}
    or None when there's no current value to project from."""
    value, _, _ = current_value(conn, item_id, condition)
    if not value or value <= 0:
        return None

    g, basis = growth_mod.best_growth_estimate(conn, item_id, condition)
    if g is None:
        g, basis = 5.0, "default"
    g = max(PARAMS["clamp_min_pct"], min(PARAMS["clamp_max_pct"], g))

    item = dbq.get_item(conn, item_id)
    ph = lifecycle.phase(item["year"] if item else None,
                         item["theme"] if item else None)
    g_eff = g * PARAMS["phase_multiplier"].get(ph["phase"], 1.0)

    now_year = datetime.now().year
    horizons = {}
    for years in PARAMS["horizons_years"]:
        projected = value * (1 + g_eff / 100.0) ** years
        # One-time retirement bump if the horizon crosses the estimated
        # retirement year of a not-yet-retired set.
        if (ph["retirement_year"] and now_year < ph["retirement_year"] <= now_year + years):
            projected *= 1 + PARAMS["retirement_step_pct"] / 100.0
        band = PARAMS["band_pct"] / 100.0
        horizons[years] = {
            "value": round(projected, 2),
            "low": round(projected * (1 - band), 2),
            "high": round(projected * (1 + band), 2),
            "year": now_year + years,
        }
    return {"basis": basis, "growth_pct": round(g, 2),
            "growth_effective_pct": round(g_eff, 2),
            "phase": ph, "horizons": horizons}
