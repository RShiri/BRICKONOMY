"""What to do about a holding, not just what it is worth.

The portfolio already says what everything is worth. The question it does not
answer is whether to act. Two signals, both deliberately quiet:

  sell        growth has flattened, the item still sells briskly, and there is
              a gain to take. A set that has stopped appreciating is money
              sitting still, and one that trades often can actually be sold.
  buy window  a wishlist item whose production run is ending. Prices rise
              after retirement, so this is the last cheap stretch.

Both stay silent on thin evidence. A signal fired from two data points or a
LOW-confidence value is worse than no signal, because it invites a real
decision about real money on the strength of noise. Everything here also
carries its own numbers, so a chip can explain itself rather than being
trusted blindly.
"""
from datetime import datetime

# Below this yearly growth, a holding has stopped doing its job.
FLAT_CAGR_PCT = 3.0
# A sale needs a buyer; anything slower than this is not really sellable.
MIN_SALES_6MO = 6
# Fewer history points than this and the trend is a rumour.
MIN_HISTORY_POINTS = 3
# How near retirement counts as a closing window, in years.
BUY_WINDOW_YEARS = 1


def sell_signal(conn, item_id, paid=None, condition="new"):
    """{'fire', 'reasons', ...} for a holding, or None when nothing to say."""
    from . import growth as growth_mod
    from . import velocity as velocity_mod
    from .valuation import current_value

    value, confidence, _ = current_value(conn, item_id, condition)
    if not value or confidence == "LOW":
        return None                      # too little behind the number
    series = growth_mod.series(conn, item_id, condition)
    if len(series) < MIN_HISTORY_POINTS:
        return None                      # no trend to speak of

    cagr = growth_mod.observed_growth(conn, item_id, condition)
    v = velocity_mod.velocity(conn, item_id, condition)
    sales = v["sales"] if v else 0
    gain_pct = ((value - paid) / paid * 100.0) if paid else None

    flat = cagr is not None and cagr < FLAT_CAGR_PCT
    liquid = sales >= MIN_SALES_6MO
    in_profit = gain_pct is not None and gain_pct > 0

    reasons = []
    if flat:
        reasons.append(f"growth has flattened to {cagr:+.1f}%/yr")
    if liquid:
        reasons.append(f"sells {sales}× per 6 months, so it can be moved")
    if in_profit:
        reasons.append(f"you are up {gain_pct:+.0f}% on what you paid")

    return {
        "fire": bool(flat and liquid and in_profit),
        "reasons": reasons,
        "cagr": cagr, "sales": sales, "gain_pct": gain_pct,
        "confidence": confidence, "points": len(series),
    }


def buy_window(conn, item_id, theme=None, year=None):
    """{'fire', 'years_left', 'estimated'} for a wishlist item, or None."""
    from . import lifecycle

    if not year:
        return None
    ph = lifecycle.phase(year, theme)
    if not ph["retirement_year"]:
        return None
    years_left = ph["retirement_year"] - datetime.now().year
    return {
        "fire": 0 <= years_left <= BUY_WINDOW_YEARS,
        "years_left": years_left,
        "phase": ph["phase"],
        # Every retirement date is currently release-year plus a flat
        # allowance. The chip must say so rather than imply a published date.
        "estimated": ph.get("retirement_estimated", True),
    }
