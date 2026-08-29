"""Turn a normalized scrape into price_snapshots rows.

For each condition (new/used) we write:
  kind='sold'   — stats over the analyzer's cleaned sold listings
  kind='stock'  — stats over the cleaned current listings
  kind='market' — the PriceAnalyzer market price + confidence

The blended series (source='blended') is written separately by
analytics.valuation after all sources for an item finished.
"""
import statistics

from . import db as dbq
from .compat import PriceAnalyzer


def _stats(items):
    prices = [x["price"] for x in items if x.get("price", 0) > 0]
    if not prices:
        return None
    return {
        "price_min": min(prices),
        "price_avg": sum(x["price"] * x["qty"] for x in items) / max(1, sum(x["qty"] for x in items)),
        "price_median": statistics.median(prices),
        "price_max": max(prices),
        "listing_count": len(items),
        "total_qty": sum(x["qty"] for x in items),
    }


def minifig_floor_values(conn, set_id, currency):
    """(new, used) total value of the set's minifigs, in `currency`.

    Feeds PriceAnalyzer's minifig floor: a "used set" listing worth less than
    its own figures is a parted-out listing, not a complete set.
    """
    from . import db as dbq
    from .analytics.valuation import BLEND_CURRENCY, current_value
    from .currency import convert

    totals = {"new": 0.0, "used": 0.0}
    figs = dbq.get_set_minifigs(conn, set_id)
    if not figs:
        return 0.0, 0.0
    for fig in figs:
        for condition in ("new", "used"):
            value, _, _ = current_value(conn, fig["fig_id"], condition)
            if value:
                totals[condition] += value * max(1, fig["qty"] or 1)
    try:
        return (convert(conn, totals["new"], BLEND_CURRENCY, currency),
                convert(conn, totals["used"], BLEND_CURRENCY, currency))
    except ValueError:
        return 0.0, 0.0


def _pricing_analysis(source, data, minifig_values, full):
    """The analysis the market price is taken from.

    eBay asking prices are aspirational — the same set sits listed at double
    what it actually sells for — so once eBay sold data is available (which
    needs a signed-in session, see brickonomy.ebay_login) its value is taken
    from sold listings alone rather than PriceAnalyzer's usual 70/30 blend of
    sold and asks. BrickLink keeps the blend, and BrickOwl has no sold data
    at all, so both are untouched. Set `ebay_price_from_sold_only` to false
    to restore the standard behaviour.
    """
    from copy import deepcopy

    from .config import get_config

    if source != "ebay" or not get_config().ebay_price_from_sold_only:
        return full

    out = deepcopy(full)
    for condition in ("new", "used"):
        stats = full[condition]["stats"]["sold"]
        avg, count = stats.get("avg"), stats.get("final_count") or 0
        if not avg or count < 2:
            continue          # too little sold data — keep the normal blend
        # Take the analyzer's own cleaned sold average as the market price.
        # Emptying `stock` and re-running would not work: the formula weights
        # a stock anchor at 30–40%, and a missing anchor counts as zero,
        # dragging the result far below what the set actually sold for.
        cond = out[condition]
        cond["market_price"] = avg
        cond["range"] = (round(avg * 0.9, 2), round(avg * 1.1, 2))
        cond["buy_target"] = round(avg * 0.8, 2)
        cond["confidence"] = "HIGH" if count >= 10 else "MEDIUM"
    return out


def write_snapshots(conn, item_id, source, currency, data, scraped_at=None,
                    keep_raw=False, minifig_values=(0.0, 0.0)):
    """data: the {meta, new:{sold,stock}, used:{sold,stock}} dict.
    Returns the PriceAnalyzer result. Commits."""
    full = PriceAnalyzer(data).analyze(*minifig_values)
    analysis = _pricing_analysis(source, data, minifig_values, full)

    for condition in ("new", "used"):
        cond = analysis[condition]
        sold_clean = cond["stats"]["sold"]["clean_items"]
        # Live listings always come from the unmodified analysis: even when a
        # source is priced off sold data alone, its current offers still feed
        # the deals finder and the "best price right now" panel.
        stock_clean = full[condition]["stats"]["stock"]["clean_items"]

        for kind, items in (("sold", sold_clean), ("stock", stock_clean)):
            st = _stats(items)
            if st:
                dbq.insert_snapshot(
                    conn, item_id, source, condition, kind, currency,
                    scraped_at=scraped_at,
                    raw=items[:60] if keep_raw else None,
                    **st,
                )

        if cond["market_price"] and cond["market_price"] > 0:
            dbq.insert_snapshot(
                conn, item_id, source, condition, "market", currency,
                market_price=cond["market_price"],
                confidence=cond["confidence"],
                listing_count=cond["stats"]["sold"]["final_count"] or None,
                scraped_at=scraped_at,
            )

    conn.commit()
    return analysis
