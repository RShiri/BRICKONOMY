"""Growth metrics from the blended snapshot series and retail price."""
from datetime import datetime
from math import exp, log

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

# Two snapshots are a difference, not a trend. 76105 had one scrape in
# January and then nothing until nightly scanning began in August; the
# 530 → 482 step between those two readings annualized to -14%/yr and sent
# the forecast downhill for a retired set that is up 33% on retail. Three
# points is the least that can show whether a move is a line or a blip, and
# the rate is fitted through all of them rather than read off the ends.
MIN_POINTS = 3

# Nightly scanning leaves gaps of a day or two, and a collector who only
# scans now and then still leaves under a season between readings. A gap of
# four months means the scanner was not running, and whatever was recorded
# before it belongs to a different era of the data. Only the latest unbroken
# run of snapshots counts as observed history, so a lone early reading
# cannot anchor the fit.
MAX_GAP_DAYS = 120

# Under a year of scans the observed rate is still finding its level, so it
# is averaged with the retail-based rate, weighted by how much of a year the
# scans cover. A full year of history stands on its own.
FULL_WEIGHT_DAYS = 365


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


def recent_run(pts):
    """The latest stretch of snapshots with no gap over MAX_GAP_DAYS, one
    point per calendar day (the day's last reading). A rescan writes the
    same blended value several times a day; counting each copy would let
    today outweigh the rest of the history."""
    by_day = {}
    for ts, price, ccy in pts:
        by_day[ts.date()] = (ts, price, ccy)
    days = sorted(by_day)
    if not days:
        return []
    run = [by_day[days[-1]]]
    for i in range(len(days) - 1, 0, -1):
        if (days[i] - days[i - 1]).days > MAX_GAP_DAYS:
            break
        run.append(by_day[days[i - 1]])
    run.reverse()
    return run


def _observed(conn, item_id, condition="new"):
    """(annualized pct, span_days) from a log-linear fit through the latest
    unbroken run of snapshots, or (None, 0) when that run is too short."""
    pts = recent_run(series(conn, item_id, condition))
    if len(pts) < MIN_POINTS or any(p <= 0 for _, p, _ in pts):
        return None, 0
    t0 = pts[0][0]
    span_days = (pts[-1][0] - t0).days
    if span_days < MIN_SPAN_DAYS:
        return None, 0
    xs = [(t - t0).total_seconds() / 86400.0 for t, _, _ in pts]
    ys = [log(p) for _, p, _ in pts]
    xm = sum(xs) / len(xs)
    ym = sum(ys) / len(ys)
    var = sum((x - xm) ** 2 for x in xs)
    if var == 0:
        return None, 0
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / var
    return (exp(slope * 365.25) - 1.0) * 100.0, span_days


def observed_growth(conn, item_id, condition="new"):
    """Annualized growth fitted through the latest unbroken run of blended
    snapshots, or None when that run has under MIN_POINTS readings or spans
    under MIN_SPAN_DAYS."""
    return _observed(conn, item_id, condition)[0]


def best_growth_estimate(conn, item_id, condition="new"):
    """Observed snapshot growth when a full year of it exists; a span-weighted
    blend of observed and retail-based CAGR while the history is younger than
    that; retail-based CAGR alone when there is no usable observed run."""
    g, span_days = _observed(conn, item_id, condition)
    _, cagr = growth_vs_retail(conn, item_id, condition)
    if g is not None:
        w = min(1.0, span_days / FULL_WEIGHT_DAYS)
        if w < 1.0 and cagr is not None:
            return w * g + (1.0 - w) * cagr, "blended"
        return g, "observed"
    if cagr is not None:
        return cagr, "retail-cagr"
    return None, None
