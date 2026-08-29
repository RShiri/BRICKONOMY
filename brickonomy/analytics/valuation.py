"""Valuation: the one official number per item, plus the source comparison.

The headline value is BrickLink's own average — the mean of its last-6-months
*sold* listings. Those are completed transactions, so they are what the set is
actually worth. Everything downstream (portfolio totals, growth, forecasts,
deals, minifig values) reads that one number through `current_value`.

The other sources are still scraped, still shown side by side on an item page,
and still power the "cheapest right now" panel — they just do not move the
valuation:

  eBay     asking prices are aspirational, routinely 2-3x what the same set
           sells for on BrickLink, and the gap does not close.
  BrickOwl a thin market; a handful of optimistic stores set the average.

`blend()` remains the source-comparison view and marks which sources are
excluded from the value. Headline rows are persisted under source='blended'
so the existing history/chart/delta queries keep reading one series.
"""
from datetime import datetime, timedelta

from .. import db as dbq
from ..currency import convert

CONFIDENCE_WEIGHT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
BLEND_CURRENCY = "ILS"          # blended snapshots are stored in this currency
MAX_SOURCE_AGE_DAYS = 14        # ignore sources with no recent scan

SOURCES = ("bricklink", "ebay", "brickowl")

# Where the official number comes from, and the order it degrades in when an
# item has no sold history yet (a set released last month, a rare fig that has
# never traded). Each step is labelled so the UI can say which one was used.
HEADLINE_SOURCE = "bricklink"
HEADLINE_BASIS = (
    ("sold", "price_avg", "BrickLink 6-month sold average"),
    ("market", "market_price", "BrickLink market price (no sold history)"),
    ("stock", "price_avg", "BrickLink current asking average (never sold)"),
)


def latest_per_source(conn, item_id, condition):
    """{source: snapshot_row} of the freshest market snapshot per source."""
    out = {}
    for source in SOURCES:
        row = dbq.latest_snapshot(conn, item_id, source, condition, kind="market")
        if row and row["market_price"]:
            out[source] = row
    return out


def blend(conn, item_id, condition, max_age_days=MAX_SOURCE_AGE_DAYS):
    """Confidence-weighted blended market price in BLEND_CURRENCY.

    Returns (value, confidence, per_source) or (None, None, per_source);
    per_source: {source: {'price', 'native', 'currency', 'confidence', 'scraped_at'}}.
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)
    per_source = {}
    weighted_sum, weight_total = 0.0, 0
    best_confidence = "LOW"

    for source, row in latest_per_source(conn, item_id, condition).items():
        try:
            age_ok = datetime.fromisoformat(row["scraped_at"]) >= cutoff
        except (TypeError, ValueError):
            age_ok = True
        converted = convert(conn, row["market_price"], row["currency"], BLEND_CURRENCY)
        per_source[source] = {
            "price": converted,
            "native": row["market_price"],
            "currency": row["currency"],
            "confidence": row["confidence"],
            "scraped_at": row["scraped_at"],
            "stale": not age_ok,
            # Shown struck-through in the comparison table: scraped and
            # displayed for reference, but it does not move the valuation.
            "excluded": source != HEADLINE_SOURCE,
        }
        if not age_ok or not converted or converted <= 0:
            continue
        w = CONFIDENCE_WEIGHT.get(row["confidence"], 1)
        weighted_sum += converted * w
        weight_total += w
        if CONFIDENCE_WEIGHT.get(row["confidence"], 1) > CONFIDENCE_WEIGHT.get(best_confidence, 1):
            best_confidence = row["confidence"]

    if weight_total == 0:
        return None, None, per_source
    return weighted_sum / weight_total, best_confidence, per_source


def headline_value(conn, item_id, condition, max_age_days=MAX_SOURCE_AGE_DAYS):
    """The official value: BrickLink's own average, in BLEND_CURRENCY.

    Returns (value, confidence, basis_label, scraped_at); value is None when
    BrickLink has no usable figure at all. Walks HEADLINE_BASIS in order, so a
    set with no sold history still gets a number and the UI can say so.
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)

    for kind, column, label in HEADLINE_BASIS:
        row = dbq.latest_snapshot(conn, item_id, HEADLINE_SOURCE, condition,
                                  kind=kind)
        if not row or not row[column] or row[column] <= 0:
            continue
        value = convert(conn, row[column], row["currency"], BLEND_CURRENCY)
        if not value or value <= 0:
            continue
        try:
            fresh = datetime.fromisoformat(row["scraped_at"]) >= cutoff
        except (TypeError, ValueError):
            fresh = True
        # A sold average over a handful of sales is thinner evidence than one
        # over dozens, and a stale scrape is weaker than a fresh one.
        count = row["total_qty"] or row["listing_count"] or 0
        if not fresh:
            confidence = "LOW"
        elif kind == "sold":
            confidence = "HIGH" if count >= 10 else "MEDIUM" if count >= 3 else "LOW"
        else:
            confidence = row["confidence"] or "LOW"
        return round(value, 2), confidence, label, row["scraped_at"]

    return None, None, None, None


def store_blended(conn, item_id):
    """Persist the headline value for both conditions under source='blended'.
    Returns {condition: value}."""
    out = {}
    for condition in ("new", "used"):
        value, confidence, _, _ = headline_value(conn, item_id, condition)
        if value and value > 0:
            dbq.insert_snapshot(
                conn, item_id, "blended", condition, "market", BLEND_CURRENCY,
                market_price=round(value, 2), confidence=confidence,
            )
            out[condition] = round(value, 2)
    conn.commit()
    return out


def current_value(conn, item_id, condition="new"):
    """The official value: latest stored headline row, else computed live.

    Kept to three return values because every caller on the site unpacks it
    that way; `headline_detail` is the richer view for an item page.
    """
    row = dbq.latest_snapshot(conn, item_id, "blended", condition, kind="market")
    if row and row["market_price"]:
        return row["market_price"], row["confidence"], row["scraped_at"]
    value, confidence, _, ts = headline_value(conn, item_id, condition)
    return value, confidence, ts


def headline_detail(conn, item_id, condition="new"):
    """current_value plus the basis label, for the item page byline."""
    value, confidence, basis, ts = headline_value(conn, item_id, condition)
    if value:
        return {"value": value, "confidence": confidence, "basis": basis,
                "as_of": ts, "source": HEADLINE_SOURCE}
    return None
