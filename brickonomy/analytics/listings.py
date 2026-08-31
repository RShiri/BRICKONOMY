"""The live offers behind a price — and which of them to believe.

Every page that says "you could buy this today" goes through here: the deals
finder, the part-out leaderboard and the set page's cheapest-price panel. It
lives in analytics rather than the web layer because the web layer is a view
over this, not the other way round.
"""
import json
import statistics
import re

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
    # Nouns that are the *subject* of the listing rather than something a set
    # contains. "Statuette" caught the 90398pb trophies; "helmet" caught a
    # Green Goblin helmet listed under set 4851.
    "statuette", "helmet", "torso", "headgear", "keychain", "key chain",
    "magnet", "poster", "box only", "empty box", "manual only",
    "instructions only", "figure only", "minifig only",
    # "X from set N" is only ever said of a piece taken out of one. A whole
    # set is never "from" a set. This caught a Nick Fury figure listed under
    # 76354 that carried no catalogue id to give it away.
    "minifigure from set", "minifig from set", "figure from set",
    "minifigure from lego set", "from set no", "taken from set",
)
# Part names in these catalogues carry dimensions; set names never do.
DIMENSION_MARKERS = ("ø", " x 1.", " x 2.", " x 9.9")

# A minifigure's own catalogue id — sh1131, spd006, cty1837, col334. A listing
# for a *set* that quotes one of these is selling the figure, not the set:
# "LEGO - Minifigs - Super Heroes - sh1131 - Phil Coulson (76354)" was priced
# as though it were the whole Helicarrier.
MINIFIG_ID_IN_TEXT = re.compile(r"\b(?:sh|sw|cty|col|spd|hp|njo|tlm)\d{3,4}[a-z]?\b")


def looks_like_a_component(description, item_type="S"):
    """True when the listing describes a part or figure, not the item priced.

    Only meaningful for sources whose listings are free text — eBay searches
    anything, and BrickOwl matches numbers across item types. BrickLink pins
    its request to `catalogitem.page?S=<id>`, so its listings are the set by
    construction, which is why it stores no description and needs none.
    """
    text = (description or "").lower()
    if not text:
        return False               # no description is not evidence either way
    if any(m in text for m in COMPONENT_MARKERS):
        return True
    if any(m in text for m in DIMENSION_MARKERS):
        return True
    # Only when pricing a set: on a minifig's own page such an id is correct.
    return item_type == "S" and bool(MINIFIG_ID_IN_TEXT.search(text))


# A cheapest ask that no other seller comes near. BrickLink's price guide for
# set 76060 carried 60 asks with a median of ₪150, and the lowest was ₪33.63 —
# 22% of the median, and 41% below the next one up at ₪57.43. Nobody sells a
# ₪150 set for ₪33; it is a mis-filed part, a wrong currency, or a listing for
# something other than the set. That one row put 76060 top of the part-out
# leaderboard at a 424% margin.
#
# The test is isolation rather than cheapness. Where two sellers independently
# sit at ₪12 against a ₪67 median, that is a market — thin, but real, and
# exactly the bargain this app exists to surface. Where one sits alone at ₪26
# with the next at ₪96, it is an error. Same principle as the rule that a
# value not backed by completed sales needs more than one seller behind it.
LONE_ASK_VS_MEDIAN = 0.5        # must also be under half the median
LONE_ASK_VS_NEXT = 0.6          # ...and under 60% of the next cheapest
LONE_ASK_MIN_SAMPLE = 5         # below this there is no median worth trusting


def drop_lone_lowball(listings):
    """`listings` sorted by price, minus any isolated cheapest asks.

    Applied repeatedly: two bad rows in a row are rare but not impossible, and
    dropping only the first would leave the second as the new "cheapest".
    Never empties the list — the last survivor stands whatever it looks like.
    """
    prices = sorted(l["price"] for l in listings)
    if len(prices) < LONE_ASK_MIN_SAMPLE:
        return listings
    cut = 0
    while len(prices) - cut >= LONE_ASK_MIN_SAMPLE:
        rest = prices[cut:]
        median = statistics.median(rest)
        if not (rest[0] < median * LONE_ASK_VS_MEDIAN
                and rest[0] < rest[1] * LONE_ASK_VS_NEXT):
            break
        cut += 1
    if not cut:
        return listings
    floor = prices[cut]
    return [l for l in listings if l["price"] >= floor]


def cheapest_stock(conn, item_id, condition="new", item_type=None):
    """{source: {price, currency, description, scraped_at}} from the latest
    stock snapshot's retained raw listings."""
    from .. import db as dbq
    from ..importer import item_type_for

    item_type = item_type or item_type_for(item_id)
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
                 and not looks_like_a_component(l.get("description"), item_type)]
        clean = drop_lone_lowball(clean)
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


def believable_offers(conn, item_id, value_ils, condition="new", to_ccy="ILS",
                      item_type=None):
    """cheapest_stock, minus listings too cheap to be the item in question.

    `value_ils` is the item's market value in ILS; offers are compared against
    it after conversion, so a mismatch is caught whatever currency it is in.
    """
    from ..currency import convert

    out = {}
    for source, o in cheapest_stock(conn, item_id, condition, item_type).items():
        try:
            ask_ils = convert(conn, o["price"], o["currency"], "ILS")
        except ValueError:
            continue
        if plausible(ask_ils, value_ils):
            out[source] = o
    return out


def landed_cost(conn, price, currency, to_ccy="ILS"):
    """What a listing really costs delivered, in `to_ccy`.

    Anything quoted in a foreign currency is treated as an import: VAT applies
    on arrival and something has to pay for shipping. A domestic listing is
    just its price.
    """
    from ..config import get_config
    from ..currency import convert

    cfg = get_config()
    base = convert(conn, price, currency, to_ccy)
    if currency == "ILS":
        return base, 0.0
    vat = base * cfg.import_vat_pct / 100.0
    shipping = convert(conn, cfg.import_shipping_usd, "USD", to_ccy)
    return base + vat + shipping, vat + shipping
