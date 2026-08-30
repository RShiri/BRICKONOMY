"""The live offers behind a price — and which of them to believe.

Every page that says "you could buy this today" goes through here: the deals
finder, the part-out leaderboard and the set page's cheapest-price panel. It
lives in analytics rather than the web layer because the web layer is a view
over this, not the other way round.
"""
import json

# A live ask below this fraction of the item's value is a mismatched listing,
# not a bargain. BrickOwl matches some set numbers against parts that share the
# number — 11211 is both a set and a very common brick — which produced
# three-agora "sets" worth ₪1,000 topping every ranked list at margins in the
# millions of percent. Real discounts in this data run 10–60%; 2% is far below
# anything a seller would actually accept.
IMPLAUSIBLE_ASK_RATIO = 0.02

# The ratio floor only catches mismatches cheap *relative to the set*. It misses
# a sticker sheet for a ₪49 polybag, so the description gets a look too. These
# are the giveaways actually observed in the data:
#
#   "New LEGO Sticker Sheet for Set 5002145"     — an accessory, not the set
#   "New Black Wheel Rim Ø14.6 x 9.9"            — a single part; its price was
#                                                  even parsed out of the "14.6"
#                                                  in the dimensions
#
# PriceAnalyzer.BLACKLIST already rejects *incomplete sets* ("no minifigs",
# "build only"); this is the different question of whether the listing is a set
# at all. A heuristic, and a stopgap: the real fix is the BrickOwl scraper
# pinning its lookups to the Set item type, which is filed separately.
COMPONENT_MARKERS = (
    "sticker sheet", "sticker set", "instruction manual", "instructions for",
    "wheel rim", "minifigure head", "minifigure torso", "minifigure legs",
    "baseplate only", "replacement part", "single part", "spare part",
)
# Part names in these catalogues carry dimensions; set names never do.
DIMENSION_MARKERS = ("ø", " x 1.", " x 2.", " x 9.9")


def looks_like_a_component(description):
    """True when the listing describes a part or accessory, not the item."""
    text = (description or "").lower()
    if not text:
        return False               # no description is not evidence either way
    return (any(m in text for m in COMPONENT_MARKERS)
            or any(m in text for m in DIMENSION_MARKERS))


def cheapest_stock(conn, item_id, condition="new"):
    """{source: {price, currency, description, scraped_at}} from the latest
    stock snapshot's retained raw listings."""
    from .. import db as dbq

    out = {}
    for source in ("bricklink", "ebay", "brickowl"):
        row = dbq.latest_snapshot(conn, item_id, source, condition, kind="stock")
        if not row or not row["raw_json"]:
            continue
        try:
            listings = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        clean = [l for l in listings
                 if l.get("price", 0) > 0
                 and not looks_like_a_component(l.get("description"))]
        if not clean:
            continue
        best = min(clean, key=lambda l: l["price"])
        out[source] = {"price": best["price"], "currency": row["currency"],
                       "description": (best.get("description") or "")[:110],
                       "scraped_at": row["scraped_at"]}
    return out


def plausible(ask, value):
    """Is this ask believable as the same item the value describes?"""
    if not value or value <= 0 or not ask or ask <= 0:
        return False
    return ask >= value * IMPLAUSIBLE_ASK_RATIO


def believable_offers(conn, item_id, value_ils, condition="new", to_ccy="ILS"):
    """cheapest_stock, minus listings too cheap to be the item in question.

    `value_ils` is the item's market value in ILS; offers are compared against
    it after conversion, so a mismatch is caught whatever currency it is in.
    """
    from ..currency import convert

    out = {}
    for source, o in cheapest_stock(conn, item_id, condition).items():
        try:
            ask_ils = convert(conn, o["price"], o["currency"], "ILS")
        except ValueError:
            continue
        if plausible(ask_ils, value_ils):
            out[source] = o
    return out
