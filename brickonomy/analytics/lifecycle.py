"""Retirement lifecycle phases.

Mirrors the age heuristic buried in the root PriceAnalyzer, extended with an
estimated retirement year and the post-retirement phases BrickEconomy's data
shows (bump at retirement → 6–24 months acceleration → stabilization).
"""
from datetime import datetime

# Themes that typically retire faster than the ~3-year default.
SHORT_LIFE_THEMES = {"seasonal", "promotional", "brickheadz", "polybag", "gwp"}

PHASES = ("NEW", "EOL WATCH", "RETIRED_ACCEL", "RETIRED_STABLE")


def estimated_retirement_year(year_released: int, theme: str = None) -> int:
    life = 2 if theme and theme.strip().lower() in SHORT_LIFE_THEMES else 3
    return year_released + life


def phase(year_released, theme=None, now=None):
    """Returns {phase, label, desc, retirement_year, age}."""
    if not year_released:
        return {"phase": None, "label": "Unknown", "desc": "Release year not known",
                "retirement_year": None, "age": None}
    now_year = (now or datetime.now()).year
    age = now_year - year_released
    ret_year = estimated_retirement_year(year_released, theme)
    years_since_ret = now_year - ret_year

    if years_since_ret < -1:
        p, label, desc = "NEW", "Available", "In production — flooded market"
    elif years_since_ret < 0:
        p, label, desc = "EOL WATCH", "Retiring soon", "Production likely ending"
    elif years_since_ret <= 2:
        p, label, desc = "RETIRED_ACCEL", "Retired — acceleration", \
            "Post-retirement appreciation window (0–2 years)"
    else:
        p, label, desc = "RETIRED_STABLE", "Retired — steady growth", \
            "Stabilized long-term growth"
    return {"phase": p, "label": label, "desc": desc,
            "retirement_year": ret_year, "age": age}
