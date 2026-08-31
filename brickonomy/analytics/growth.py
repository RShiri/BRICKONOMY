"""Growth metrics from the blended snapshot series and retail price."""
from datetime import datetime

from .. import db as dbq
from ..currency import convert

# Annualizing turns the observed span into a yearly rate by raising it to the
# power of 365/span, so a short window multiplies its own noise. At 7 days that
# factor is 52: set 76051 drifted from 461.60 to 485.94 over a fortnight — a
# 5% wiggle between two scrapes — and came out as +249%/yr, which put it top of
# the growth sort on the Sets page and made it its theme's best performer.
#
# 90 days is the usual floor for quoting an annualized rate. It costs four
# items here, and those four are exactly the noisy ones (+249%, -47%, -31%,
# +14%, all from spans under three weeks); every rate that survives is inside
# ±100%/yr. An item below the floor falls back to growth against retail, which
# is the sounder long-run measure anyway.
MIN_SPAN_DAYS = 90


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
