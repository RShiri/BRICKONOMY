"""Growth metrics from the blended snapshot series and retail price."""
from datetime import datetime

from .. import db as dbq
from ..currency import convert

MIN_SPAN_DAYS = 7   # snapshot-to-snapshot growth needs at least this span


def series(conn, item_id, condition="new", source="blended"):
    """[(datetime, price, currency), ...] ordered by time."""
    rows = dbq.snapshot_history(conn, item_id, condition=condition,
                                kind="market", sources=[source])
    out = []
    for r in rows:
        if not r["market_price"]:
            continue
        try:
            ts = datetime.fromisoformat(r["scraped_at"])
        except (TypeError, ValueError):
            continue
        out.append((ts, r["market_price"], r["currency"]))
    return out


def growth_vs_retail(conn, item_id, condition="new"):
    """(pct_total, pct_annualized) vs retail, in retail's own currency terms.
    None when retail or current value is missing."""
    item = dbq.get_item(conn, item_id)
    if not item or not item["retail_price"] or not item["year"]:
        return None, None
    pts = series(conn, item_id, condition)
    if not pts:
        return None, None
    _, current, ccy = pts[-1]
    retail_conv = convert(conn, item["retail_price"], item["retail_currency"] or "USD", ccy)
    if not retail_conv or retail_conv <= 0:
        return None, None

    total = (current - retail_conv) / retail_conv * 100.0
    years = max(1, datetime.now().year - item["year"])
    cagr = ((current / retail_conv) ** (1.0 / years) - 1.0) * 100.0
    return total, cagr


def observed_growth(conn, item_id, condition="new"):
    """Annualized growth between the first and last blended snapshots,
    or None when the span is under MIN_SPAN_DAYS."""
    pts = series(conn, item_id, condition)
    if len(pts) < 2:
        return None
    (t0, p0, _), (t1, p1, _) = pts[0], pts[-1]
    span_days = (t1 - t0).days
    if span_days < MIN_SPAN_DAYS or p0 <= 0:
        return None
    years = span_days / 365.25
    return ((p1 / p0) ** (1.0 / years) - 1.0) * 100.0


def best_growth_estimate(conn, item_id, condition="new"):
    """Observed snapshot growth when available, else retail-based CAGR."""
    g = observed_growth(conn, item_id, condition)
    if g is not None:
        return g, "observed"
    _, cagr = growth_vs_retail(conn, item_id, condition)
    if cagr is not None:
        return cagr, "retail-cagr"
    return None, None
