"""Which sets are worth buying to break up — ranked.

BrickLink tells you what a set's parts are worth. BrickEconomy shows a part-out
value per set. Neither answers the question a buyer actually has: *of everything
I could buy today, which one makes the most money broken up?* That needs the
live ask and the part-out value in the same row, and it needs both to be
honest about what they cost and what they yield.

Three things are modelled that a raw "POV vs price" comparison ignores, and
each one moves the answer a long way:

  Landed cost   A $162 listing is not ₪600. Importing to Israel adds VAT and
                shipping, and a set that looks cheap abroad often is not.
                Domestic listings are unaffected.
  Fees          Selling costs money: BrickLink commission plus payment
                handling. Revenue is not the part-out total.
  Realisation   Nobody sells every lot. A part-out total is the theoretical
                maximum if every brick eventually finds a buyer, which for the
                long tail of common parts means years or never. The default
                assumes a realistic fraction, and the page says so rather than
                quoting a number nobody achieves.

Sets whose part-out data is stale are flagged rather than dropped: a
three-month-old POV is still a signal, just a weaker one.
"""
from datetime import datetime, timedelta

# What actually reaches your pocket. Deliberately conservative — the point of
# this page is to be right, not encouraging.
SELLER_FEE_RATE = 0.10        # BrickLink commission + payment handling
REALISATION_RATE = 0.70       # share of the part-out total that ever sells
STALE_POV_DAYS = 30


def opportunities(conn, ccy="ILS", limit=None, max_budget=None,
                  include_stale=True):
    """Ranked part-out opportunities. Most profitable first.

    Each row: what it costs delivered, what breaking it up realistically
    yields, the profit and margin between them, and how much of the value is
    minifigures — the part of a set that sells fastest.
    """
    from ..currency import convert
    from . import velocity as velocity_mod
    from .listings import believable_offers, landed_cost
    from .valuation import current_value

    cutoff = datetime.now() - timedelta(days=STALE_POV_DAYS)
    out = []
    for pov in conn.execute(
            """SELECT po.set_id, po.pov_total, po.currency, po.scraped_at,
                      i.name, i.theme, i.year, i.parts
               FROM part_out po JOIN items i ON i.item_id = po.set_id"""):
        set_id = pov["set_id"]
        # The set's own market value is the yardstick for believing an ask:
        # a three-agora "set" is a mismatched part listing, not an opportunity.
        whole, _, _ = current_value(conn, set_id, "new")
        offers = believable_offers(conn, set_id, whole, "new")
        if not offers:
            continue

        # Cheapest by what it actually costs delivered, not by sticker price:
        # a $50 US listing can land dearer than a ₪250 domestic one.
        priced = {}
        for source, o in offers.items():
            try:
                cost, extra = landed_cost(conn, o["price"], o["currency"], ccy)
            except ValueError:
                continue
            if cost and cost > 0:
                priced[source] = (cost, extra, o)
        if not priced:
            continue
        source = min(priced, key=lambda s: priced[s][0])
        cost, import_extra, offer = priced[source]
        if max_budget and cost > max_budget:
            continue

        try:
            pov_total = convert(conn, pov["pov_total"], pov["currency"], ccy)
        except ValueError:
            continue
        if not pov_total or pov_total <= 0:
            continue

        # Selling it whole is the alternative use of the same money.
        whole_ccy = convert(conn, whole, "ILS", ccy) if whole else None

        revenue = pov_total * REALISATION_RATE * (1 - SELLER_FEE_RATE)
        profit = revenue - cost
        margin = profit / cost * 100.0

        try:
            stale = datetime.fromisoformat(pov["scraped_at"]) < cutoff
        except (TypeError, ValueError):
            stale = True
        if stale and not include_stale:
            continue

        # Minifigures are the liquid half of a part-out: they sell in days,
        # loose bricks in years. A high fig share is a materially better
        # opportunity at the same margin.
        figs = conn.execute(
            """SELECT sm.fig_id, sm.qty FROM set_minifigs sm
               WHERE sm.set_id = ?""", (set_id,)).fetchall()
        fig_value, fig_priced = 0.0, 0
        for f in figs:
            fv, _, _ = current_value(conn, f["fig_id"], "used")
            if fv:
                fig_priced += 1
                fig_value += convert(conn, fv, "ILS", ccy) * (f["qty"] or 1)
        fig_share = fig_value / pov_total * 100.0 if pov_total else None

        v = velocity_mod.velocity(conn, set_id, "new")
        out.append({
            "item_id": set_id, "name": pov["name"], "theme": pov["theme"],
            "year": pov["year"], "parts": pov["parts"],
            "source": source, "ask": convert(conn, offer["price"],
                                             offer["currency"], ccy),
            "ask_currency": offer["currency"],
            "cost": cost, "import_extra": import_extra,
            "imported": offer["currency"] != "ILS",
            "pov": pov_total, "revenue": revenue,
            "profit": profit, "margin": margin,
            "whole": whole_ccy,
            "better_whole": bool(whole_ccy and whole_ccy > revenue),
            "fig_value": fig_value if fig_priced else None,
            "fig_share": fig_share if fig_priced else None,
            "figs_priced": fig_priced, "figs_total": len(figs),
            "velocity": v,
            "adjusted_margin": margin * velocity_mod.liquidity_factor(v),
            "stale": stale, "pov_as_of": pov["scraped_at"],
        })

    out.sort(key=lambda r: r["adjusted_margin"], reverse=True)
    return out[:limit] if limit else out
