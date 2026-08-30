"""How often an item actually sells.

A price with no sales rate behind it is a fantasy. "₪486" means one thing when
the set trades weekly and quite another when it has moved twice in six months
— the second is a number nobody will pay you on demand. BrickEconomy prints
values without ever saying how liquid they are; this is the half they omit.

The data was already being scraped and stored, just never surfaced: the
BrickLink price guide's sold table is a rolling six-month window, and
`price_snapshots` keeps its `listing_count` (how many sales) and `total_qty`
(how many units, since one sale can move several).

BrickLink only, deliberately. eBay's sold counts come from a signed-in search
that is capped at a page of results, so its volume says more about the scrape
than the market; BrickOwl publishes no sold data at all.
"""
SOURCE = "bricklink"
WINDOW_MONTHS = 6

# Sales per six months, mapped to how it feels to actually sell one.
BANDS = (
    (26, "weekly", "sells about weekly"),
    (6, "monthly", "sells about monthly"),
    (2, "slow", "a few sales in six months"),
    (0, "rare", "barely trades"),
)


def band_for(sales):
    """(key, phrase) for a sales-per-6-months count."""
    for threshold, key, phrase in BANDS:
        if sales >= threshold:
            return key, phrase
    return "rare", "barely trades"


def velocity(conn, item_id, condition="new"):
    """{'sales', 'units', 'band', 'phrase', 'per_month', 'as_of'} or None.

    None means no sold data has been scraped — which is not the same as "does
    not sell", and the UI must not render it as zero.
    """
    from .. import db as dbq

    row = dbq.latest_snapshot(conn, item_id, SOURCE, condition, kind="sold")
    if row is None or row["listing_count"] is None:
        return None
    sales = row["listing_count"] or 0
    key, phrase = band_for(sales)
    return {
        "sales": sales,
        "units": row["total_qty"] or sales,
        "band": key,
        "phrase": phrase,
        "per_month": round(sales / WINDOW_MONTHS, 1),
        "as_of": row["scraped_at"],
    }


def liquidity_factor(v):
    """0.5 – 1.0, for discounting a paper margin by how sellable the item is.

    A 40% margin on something that trades weekly is worth more than 40% on
    something that moves twice a year, because the second one ties up money
    for months and may not sell at the modelled price at all. Unknown velocity
    scores neutral-low rather than optimistic: absence of evidence is not
    evidence of liquidity.
    """
    if not v:
        return 0.75
    return {"weekly": 1.0, "monthly": 0.9, "slow": 0.7, "rare": 0.5}[v["band"]]
