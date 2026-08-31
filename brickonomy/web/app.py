"""Brickonomy web app.

Run:  uvicorn brickonomy.web.app:app --reload
"""
import csv
import io
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db as dbq
from ..analytics import forecast as forecast_mod
from ..analytics import growth as growth_mod
from ..analytics import lifecycle
from ..analytics import partout as partout_mod
from ..analytics import signals as signals_mod
from ..analytics import velocity as velocity_mod
from ..analytics.listings import (IMPLAUSIBLE_ASK_RATIO, cheapest_stock,
                                  landed_cost)
from ..analytics.valuation import blend, current_value
from ..compat import LEGACY_DB_PATH
from ..config import SUPPORTED_CURRENCIES, get_config
from ..currency import CURRENCY_SYMBOLS, convert, money, rates_status
from ..importer import (item_type_for, normalize_item_id, parse_condition,
                        parse_money, sold_off)
from .. import refresh as refresh_mod
from . import jobs

BASE_DIR = Path(__file__).resolve().parent

# Static-export mode: brickonomy.export flips these, then crawls the app with
# a TestClient. Links become RELATIVE .html paths (so the exported site works
# at any mount point — repo root, /docs, a custom domain) and server-only UI
# (refresh/import/edit/currency) is hidden.
STATIC_MODE = bool(os.environ.get("BRICKONOMY_STATIC_EXPORT"))
# Directory depth of the page currently being rendered, e.g. sets/75192.html
# has depth 1. The exporter sets this before each request.
STATIC_DEPTH = 0
# Item ids that get their own exported page. Everything else in the catalog
# links to the client-rendered set.html instead, so no link ever dangles.
STATIC_PAGED_IDS = None


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", (text or "").lower())).strip("-")


def static_prefix() -> str:
    """'' at the site root, '../' one level down, and so on."""
    return "../" * STATIC_DEPTH


def static_url(path: str) -> str:
    """Rewrite an app route to its exported static file, relative to the page
    being rendered."""
    if not STATIC_MODE:
        return path
    path, _, query = path.partition("?")
    if path == "/":
        rel = "index.html"
    elif path == "/sets":
        m = re.search(r"theme=([^&]+)", query)
        if m:
            from urllib.parse import unquote_plus
            rel = f"sets/theme-{slugify(unquote_plus(m.group(1)))}.html"
        else:
            rel = "sets/index.html"
    elif path == "/portfolio":
        rel = "portfolio.html"
    elif path == "/set":
        rel = "set.html"
    elif path.startswith("/api/"):
        rel = f"{path.lstrip('/')}.json"
    elif path.startswith("/static/"):
        rel = path.lstrip("/")
    elif path.startswith("/sets/"):
        item_id = path[len("/sets/"):]
        if STATIC_PAGED_IDS is not None and item_id not in STATIC_PAGED_IDS:
            rel = f"set.html?id={quote(item_id)}"
        else:
            rel = f"sets/{item_id}.html"
    else:
        rel = f"{path.lstrip('/')}.html"
    return static_prefix() + rel


app = FastAPI(title="Brickonomy")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["money"] = money


# ── helpers ──────────────────────────────────────────────────────────────

def get_conn():
    return dbq.connect()


def display_ccy(request: Request) -> str:
    ccy = request.cookies.get("ccy", get_config().display_currency).upper()
    return ccy if ccy in SUPPORTED_CURRENCIES else get_config().display_currency


# How much of the collection must have been scanned before a day is worth
# plotting. Below this the total is dominated by which sets happen to have a
# price yet rather than by what they are worth.
PORTFOLIO_CHART_COVERAGE = 0.9
# ...but never trim below this many days. A one-point chart is not a chart,
# and its time axis degenerates to a millisecond.
PORTFOLIO_CHART_MIN_POINTS = 4

# How many items /compare will hold. Four columns still read on a phone once
# the table scrolls; more turns the comparison into a spreadsheet.
COMPARE_MAX = 4


def clamp(value, low, high, default=0.0):
    """A number from a query string, held inside the range the form offers.

    FastAPI will happily parse "1e400" into inf and "nan" into a NaN, and both
    then travel into the page: inf reached Jinja's |int filter and raised
    OverflowError, a 500 from a hand-typed URL, and NaN compares false against
    everything so it silently disabled the filter it was meant to apply.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value:                      # NaN, which loses every comparison
        return default
    return max(low, min(high, value))


def img_url(item_id: str, item_type: str = "S") -> str:
    if item_type == "M" or any(c.isalpha() for c in item_id):
        return f"https://img.bricklink.com/ItemImage/MN/0/{item_id}.png"
    suffix = item_id if "-" in item_id else f"{item_id}-1"
    return f"https://img.bricklink.com/ItemImage/SN/0/{suffix}.png"


def asset_version():
    """A cache-busting stamp for the CSS and JS, from their own mtimes.

    Both are served with far-future caching by the browser, and the static
    export puts them behind GitHub Pages' CDN, so without this a returning
    visitor keeps whatever stylesheet they first downloaded — a layout fix
    ships and nobody who has already visited ever sees it.
    """
    stamp = 0
    for name in ("style.css", "app.js"):
        try:
            stamp = max(stamp, int((BASE_DIR / "static" / name).stat().st_mtime))
        except OSError:
            pass
    return str(stamp)


def ctx(request: Request, conn, **extra):
    ccy = display_ccy(request)
    return {
        "request": request,
        "ccy": ccy,
        "ccy_symbol": CURRENCY_SYMBOLS.get(ccy, ccy),
        "currencies": SUPPORTED_CURRENCIES,
        "rates": rates_status(),
        "job": jobs.status(),
        "img_url": img_url,
        "static_mode": STATIC_MODE,
        "base_path": static_prefix(),
        "u": static_url,
        "asset_v": asset_version(),
        **extra,
    }


def disp(conn, amount, from_ccy, to_ccy):
    if amount is None:
        return None
    try:
        return convert(conn, amount, from_ccy, to_ccy)
    except ValueError:
        return None


def item_view(conn, row, ccy):
    """Common per-item view model for lists."""
    iid = row["item_id"]
    val_new, conf, _ = current_value(conn, iid, "new")
    # The used confidence was being thrown away, so a used value resting on a
    # single sale sat beside a new one resting on thirty-seven and looked just
    # as solid. That is how 40 items came to show used worth more than new.
    val_used, conf_used, _ = current_value(conn, iid, "used")
    g, g_basis = growth_mod.best_growth_estimate(conn, iid)
    ph = lifecycle.phase(row["year"], row["theme"] if "theme" in row.keys() else None)
    return {
        "item_id": iid,
        "item_type": row["item_type"] if "item_type" in row.keys() else item_type_for(iid),
        "name": row["name"],
        "theme": row["theme"] if "theme" in row.keys() else None,
        "year": row["year"],
        # Both: the converted figure drives comparisons, the native one is
        # what LEGO actually charged. Converting an MSRP into shekels invents
        # a price that never existed on any shelf.
        "retail": disp(conn, row["retail_price"],
                       row["retail_currency"] or "USD", ccy) if "retail_price" in row.keys() and row["retail_price"] else None,
        "retail_native": row["retail_price"] if "retail_price" in row.keys() else None,
        "retail_ccy": (row["retail_currency"] or "USD") if "retail_price" in row.keys() else None,
        "value_new": disp(conn, val_new, "ILS", ccy),
        "value_used": disp(conn, val_used, "ILS", ccy),
        "confidence": conf,
        "confidence_used": conf_used,
        "growth": g,
        "growth_basis": g_basis,
        "delta30": dbq.market_delta(conn, iid, days=30),
        "phase": ph,
        "spark": spark_points(conn, iid),
    }


def spark_points(conn, item_id, width=72, height=20, pad=2):
    pts = growth_mod.series(conn, item_id)[-12:]
    if len(pts) < 2:
        return None
    vals = [p for _, p, _ in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(vals) - 1)
    return " ".join(
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(vals)
    )


def legacy_minifigs(set_id):
    """Minifig inventory captured by the root scraper, if any."""
    if not LEGACY_DB_PATH.exists():
        return []
    conn = sqlite3.connect(LEGACY_DB_PATH)
    try:
        row = conn.execute(
            "SELECT json_data FROM inventory_lists WHERE set_id IN (?, ?)",
            (set_id, f"{set_id}-1"),
        ).fetchone()
        return json.loads(row[0]) if row else []
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def deal_for(conn, item_id, ccy, condition="new"):
    """BrickEconomy/sniper-style bargain check: cheapest clean listing vs the
    blended market value, after the 13% fee/VAT allowance the analyzer uses.
    Returns None when there is no live listing or no value to compare with."""
    value, confidence, _ = current_value(conn, item_id, condition)
    if not value or value <= 0:
        return None
    offers = cheapest_stock(conn, item_id, condition)
    if not offers:
        return None
    best_source = min(offers, key=lambda s: disp(conn, offers[s]["price"],
                                                 offers[s]["currency"], "ILS") or 1e18)
    o = offers[best_source]
    ask_ils = disp(conn, o["price"], o["currency"], "ILS")
    if not ask_ils or ask_ils <= 0:
        return None
    # An ask far below the market is a mismatched listing, not a bargain.
    # BrickOwl matches some set numbers against parts that share the number
    # (11211 is a set *and* a very common brick), yielding ₪0.03 "sets" that
    # otherwise top the deals list at a few million percent margin. Nobody is
    # selling a ₪1,000 set for three agorot.
    if ask_ils < value * IMPLAUSIBLE_ASK_RATIO:
        return None
    # What it costs delivered. A $162 eBay listing is ₪1,437 once VAT and
    # shipping land, which is not a deal on a set worth ₪486 however cheap the
    # sticker price looks next to a domestic one.
    try:
        cost_ils, import_extra = landed_cost(conn, o["price"], o["currency"], "ILS")
    except ValueError:
        return None
    # Margin against the landed cost, plus the 13% fee/VAT allowance the
    # analyzer applies to any purchase.
    profit = value - (cost_ils * 1.13)
    margin = profit / cost_ils * 100.0
    # A margin you cannot realise is not a margin. Rank on the liquidity-
    # adjusted figure so a 30% edge on something that trades weekly outranks
    # 45% on a set that moves twice a year and ties the money up meanwhile.
    v = velocity_mod.velocity(conn, item_id, condition)
    adjusted = margin * velocity_mod.liquidity_factor(v)
    rating = ("EXCELLENT" if adjusted >= 20 else
              "GOOD" if adjusted >= 10 else "IRRELEVANT")
    return {
        "item_id": item_id, "source": best_source,
        "ask": disp(conn, ask_ils, "ILS", ccy),
        "landed": disp(conn, cost_ils, "ILS", ccy),
        "import_extra": disp(conn, import_extra, "ILS", ccy),
        "imported": o["currency"] != "ILS",
        "value": disp(conn, value, "ILS", ccy),
        "profit": disp(conn, profit, "ILS", ccy),
        "margin": margin, "adjusted_margin": adjusted,
        "rating": rating, "confidence": confidence, "velocity": v,
    }


# ── pages ────────────────────────────────────────────────────────────────

@app.get("/")
def dashboard(request: Request):
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        rows = dbq.get_portfolio(conn)

        total_value = total_retail = 0.0
        # The gain figure has to compare like with like. It divided the value
        # of all 92 sets by the retail of the 78 that have one, which is not a
        # return on anything; and it read "paid/retail" while only ever using
        # retail, so a set bought below list looked like a loss. Both sides of
        # the ratio now cover the same rows, costed at what was paid where
        # that is known.
        basis_value = basis_cost = 0.0
        basis_rows = 0
        movers = []
        theme_totals = {}
        for row in rows:
            # The condition the set is actually held in. 76 of the 92 rows in
            # this collection are used, and valuing them as new overstated the
            # portfolio by 43% — the headline number on the landing page
            # disagreed with the portfolio page, which had it right.
            held = row["condition"] or "new"
            val, _, _ = current_value(conn, row["item_id"], held)
            v = disp(conn, val, "ILS", ccy) or 0.0
            qty = row["owned"] or 1
            total_value += v * qty
            if row["retail_price"]:
                total_retail += (disp(conn, row["retail_price"],
                                      row["retail_currency"] or "USD", ccy) or 0.0) * qty
            cost = None
            if row["purchase_price"]:
                cost = disp(conn, row["purchase_price"],
                            row["purchase_currency"] or "USD", ccy)
            elif row["retail_price"]:
                cost = disp(conn, row["retail_price"],
                            row["retail_currency"] or "USD", ccy)
            if cost:
                basis_value += v * qty
                basis_cost += cost * qty
                basis_rows += 1
            theme = row["theme"] or "Other"
            theme_totals[theme] = theme_totals.get(theme, 0.0) + v * qty
            delta = dbq.market_delta(conn, row["item_id"], condition=held,
                                     days=30)
            if delta is not None and v > 0:
                movers.append({"item_id": row["item_id"], "name": row["name"],
                               "value": v, "delta": delta})

        movers.sort(key=lambda m: m["delta"], reverse=True)
        gainers, decliners = movers[:5], sorted(movers[-5:], key=lambda m: m["delta"])
        decliners = [m for m in decliners if m["delta"] < 0]
        gainers = [m for m in gainers if m["delta"] > 0]

        themes = sorted(theme_totals.items(), key=lambda kv: kv[1], reverse=True)
        top_themes = themes[:5]
        rest = sum(v for _, v in themes[5:])
        if rest > 0:
            top_themes.append((f"Other ({len(themes) - 5} themes)", rest))
        max_theme = max((v for _, v in top_themes), default=1.0)

        counts = {
            "items": conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"],
            "portfolio": len(rows),
            "retired": sum(1 for r in conn.execute("SELECT year FROM items")
                           if r["year"] and lifecycle.phase(r["year"])["phase"] in
                           ("RETIRED_ACCEL", "RETIRED_STABLE")),
        }
        last_scan = conn.execute(
            "SELECT MAX(scraped_at) ts FROM price_snapshots WHERE source != 'blended'"
        ).fetchone()["ts"]

        gain_pct = ((basis_value - basis_cost) / basis_cost * 100.0) if basis_cost else None
        gain_abs = basis_value - basis_cost if basis_cost else None

        # Catalog-wide top lists (BrickEconomy-style leaderboards).
        catalog = []
        # Every value in one query, and only the items that have one. Asking
        # per row over the whole catalog meant 34,880 lookups to find the few
        # hundred rows that could appear in a leaderboard, and a growth
        # estimate on top for each survivor.
        catalog_values = dbq.latest_values(conn, "new")
        for row in conn.execute(
                "SELECT item_id, name, theme, year, item_type, parts, retail_price, "
                "retail_currency FROM items WHERE item_id IN ({})".format(
                    ",".join("?" * len(catalog_values)) or "NULL"),
                tuple(catalog_values)):
            v = disp(conn, catalog_values.get(row["item_id"]), "ILS", ccy)
            if not v:
                continue
            g, _ = growth_mod.best_growth_estimate(conn, row["item_id"])
            catalog.append({
                "item_id": row["item_id"], "name": row["name"], "theme": row["theme"],
                "year": row["year"], "item_type": row["item_type"],
                "value": v, "growth": g,
                "phase": lifecycle.phase(row["year"], row["theme"]),
                "ppp": v / row["parts"] if row["parts"] else None,
            })
        most_valuable = sorted(catalog, key=lambda i: i["value"], reverse=True)[:5]
        top_growth = sorted([i for i in catalog if i["growth"] is not None],
                            key=lambda i: i["growth"], reverse=True)[:5]
        retiring = sorted([i for i in catalog if i["phase"]["phase"] == "EOL WATCH"],
                          key=lambda i: i["value"], reverse=True)[:5]
        best_ppp = sorted([i for i in catalog if i["ppp"]], key=lambda i: i["ppp"])[:5]

        top_deals = []
        for item in sorted(catalog, key=lambda i: i["value"], reverse=True)[:60]:
            d = deal_for(conn, item["item_id"], ccy)
            if d and d["margin"] >= 10:
                top_deals.append({**d, "name": item["name"]})
        top_deals.sort(key=lambda d: d["margin"], reverse=True)

        return templates.TemplateResponse(request, "index.html", ctx(
            request, conn,
            total_value=total_value, total_retail=total_retail, gain_pct=gain_pct,
            gain_abs=gain_abs, gain_rows=basis_rows, owned_rows=len(rows),
            gainers=gainers, decliners=decliners,
            themes=top_themes, max_theme=max_theme,
            counts=counts, last_scan=last_scan,
            most_valuable=most_valuable, top_growth=top_growth,
            retiring=retiring, best_ppp=best_ppp, top_deals=top_deals[:5],
        ))
    finally:
        conn.close()


@app.get("/themes")
def themes_page(request: Request):
    """Theme analysis: how each LEGO theme is performing."""
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        # Sets only. A theme's size and average value are statements about
        # sets; the eight themed minifigures in the catalog would be counted
        # alongside them as if a fig and a 7,500-piece UCS set were peers.
        rows = conn.execute(
            "SELECT item_id, name, theme, year, retail_price, retail_currency "
            "FROM items WHERE item_type='S' AND theme IS NOT NULL AND theme != ''"
        ).fetchall()

        # Every value in one query rather than one query per row, and growth
        # only for the rows that have a value — the loop below already ignores
        # growth for the rest, so estimating it for 16.5k unpriced sets was
        # work whose result was thrown away.
        values = dbq.latest_values(conn, "new")

        themes = {}
        for row in rows:
            v = disp(conn, values.get(row["item_id"]), "ILS", ccy)
            g = growth_mod.best_growth_estimate(conn, row["item_id"])[0] if v else None
            t = themes.setdefault(row["theme"], {
                "theme": row["theme"], "count": 0, "valued": 0, "total": 0.0,
                "growths": [], "retail": 0.0, "best": None, "years": [],
            })
            t["count"] += 1
            if row["year"]:
                t["years"].append(row["year"])
            if v:
                t["valued"] += 1
                t["total"] += v
                if row["retail_price"]:
                    t["retail"] += disp(conn, row["retail_price"],
                                        row["retail_currency"] or "USD", ccy) or 0
                if g is not None:
                    t["growths"].append(g)
                    if not t["best"] or g > t["best"]["growth"]:
                        t["best"] = {"item_id": row["item_id"], "name": row["name"],
                                     "growth": g, "value": v}

        out = []
        for t in themes.values():
            avg_growth = sum(t["growths"]) / len(t["growths"]) if t["growths"] else None
            out.append({**t,
                        "avg_growth": avg_growth,
                        "avg_value": t["total"] / t["valued"] if t["valued"] else None,
                        "vs_retail": ((t["total"] - t["retail"]) / t["retail"] * 100.0)
                        if t["retail"] else None,
                        "span": (min(t["years"]), max(t["years"])) if t["years"] else None})
        out.sort(key=lambda t: t["total"], reverse=True)
        max_total = max((t["total"] for t in out), default=1.0) or 1.0
        growths = [t["avg_growth"] for t in out if t["avg_growth"] is not None]
        growth_scale = max((abs(g) for g in growths), default=1.0) or 1.0

        return templates.TemplateResponse(request, "themes.html", ctx(
            request, conn, themes=out, max_total=max_total, growth_scale=growth_scale,
        ))
    finally:
        conn.close()


@app.get("/partout")
def partout_page(request: Request, budget: float = 0.0, sort: str = "adjusted",
                 fresh_only: bool = False):
    """Which sets are worth buying to break up, ranked.

    The question neither BrickLink nor BrickEconomy answers: not "what are this
    set's parts worth" but "of everything buyable today, which one makes the
    most money broken up".
    """
    conn = get_conn()
    try:
        # A negative budget filters nothing and an infinite one is not a
        # budget; the input offers a plain number, so hold it to one.
        budget = clamp(budget, 0.0, 1_000_000.0)
        ccy = display_ccy(request)
        rows = partout_mod.opportunities(
            conn, ccy=ccy, max_budget=budget or None,
            include_stale=not fresh_only)
        keys = {
            "adjusted": lambda r: r["adjusted_margin"],
            "margin": lambda r: r["margin"],
            "profit": lambda r: r["profit"],
            "figs": lambda r: r["fig_share"] or -1,
        }
        rows.sort(key=keys.get(sort, keys["adjusted"]), reverse=True)
        profitable = [r for r in rows if r["profit"] > 0]
        return templates.TemplateResponse(request, "partout.html", ctx(
            request, conn, rows=rows[:150], total=len(rows),
            profitable=len(profitable), budget=budget, sort=sort,
            fresh_only=fresh_only,
            fee_pct=round(partout_mod.SELLER_FEE_RATE * 100),
            realisation_pct=round(partout_mod.REALISATION_RATE * 100),
        ))
    finally:
        conn.close()


@app.get("/deals")
def deals_page(request: Request, min_margin: float = 0.0, rating: str = ""):
    """Bargain finder: live listings priced under their blended market value."""
    conn = get_conn()
    try:
        # The form offers 0-100; the URL is not obliged to.
        min_margin = clamp(min_margin, 0.0, 100.0)
        ccy = display_ccy(request)
        deals = []
        # Only items with a live listing can be a deal, and a listing exists
        # only where a stock snapshot does — a few hundred rows, not the
        # 34,880 in the catalog. Walking all of them cost four queries each
        # (one per source in cheapest_stock, plus the value lookup): 140,141
        # queries per page load, essentially all of them asking about sets
        # that have never been scanned and cannot have an offer.
        for row in conn.execute(
                """SELECT i.item_id, i.name, i.theme, i.year, i.item_type
                   FROM items i
                   WHERE i.item_id IN (SELECT DISTINCT item_id
                                       FROM price_snapshots WHERE kind='stock')"""):
            d = deal_for(conn, row["item_id"], ccy)
            if not d or d["margin"] < min_margin:
                continue
            ph = lifecycle.phase(row["year"], row["theme"])
            if ph["phase"] in ("RETIRED_ACCEL", "RETIRED_STABLE") and d["rating"] == "GOOD":
                d["rating"] = "GREAT INVEST"
            if rating and d["rating"] != rating:
                continue
            deals.append({**d, "name": row["name"], "theme": row["theme"],
                          "item_type": row["item_type"], "phase": ph})
        # Sorted on the liquidity-adjusted margin, which is the whole point of
        # measuring velocity: a fat edge on something that never trades is
        # worth less than a thinner one you can actually turn over.
        deals.sort(key=lambda d: d["adjusted_margin"], reverse=True)
        counts = {r: sum(1 for d in deals if d["rating"] == r)
                  for r in ("EXCELLENT", "GREAT INVEST", "GOOD", "IRRELEVANT")}
        return templates.TemplateResponse(request, "deals.html", ctx(
            request, conn, deals=deals[:120], total=len(deals), counts=counts,
            min_margin=min_margin, rating=rating,
        ))
    finally:
        conn.close()


@app.get("/minifigs")
def minifigs_page(request: Request, q: str = "", filter: str = "",
                  sort: str = "growth", theme: str = ""):
    """The catalog restricted to minifigures — same listing, same filters."""
    return sets_page(request, q=q, filter=filter, sort=sort, theme=theme,
                     kind="M")


@app.get("/sets")
def sets_page(request: Request, q: str = "", filter: str = "", sort: str = "growth",
              theme: str = "", kind: str = "S"):
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        # Filtering happens in SQL: ids sort as text, so post-filtering a
        # capped fetch showed only the themes whose ids sort early.
        rows = dbq.list_items(conn, search=q, limit=400, theme=theme,
                              item_type=kind if kind in ("S", "M") else "",
                              order="value" if sort == "value" else "")
        if filter == "portfolio":
            owned = {r["item_id"] for r in dbq.get_portfolio(conn)}
            rows = [r for r in rows if r["item_id"] in owned]

        items = [item_view(conn, r, ccy) for r in rows]
        if filter == "retired":
            items = [i for i in items if i["phase"]["phase"] in ("RETIRED_ACCEL", "RETIRED_STABLE")]

        keyfns = {
            "growth": lambda i: i["growth"] if i["growth"] is not None else -1e9,
            "value": lambda i: i["value_new"] or 0,
            "year": lambda i: i["year"] or 0,
            "id": lambda i: i["item_id"],
        }
        items.sort(key=keyfns.get(sort, keyfns["growth"]),
                   reverse=sort != "id")

        # Themes for the kind on screen. Rebrickable files themes against sets,
        # not figures, so on the minifig tab this comes back nearly empty and
        # the template drops the filter rather than showing a dead control.
        themes = conn.execute(
            """SELECT theme, COUNT(*) n FROM items
               WHERE theme IS NOT NULL AND theme != '' AND item_type = ?
               GROUP BY theme ORDER BY n DESC LIMIT 30""", (kind,)
        ).fetchall()
        n_categories = conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"]
        catalog_total = conn.execute(
            "SELECT COUNT(*) c FROM items WHERE item_type = ?", (kind,)
        ).fetchone()["c"]
        # How many rows actually match, so a capped page can say what it hid.
        matched = conn.execute(
            f"""SELECT COUNT(*) c FROM items
                WHERE item_type = ?{' AND theme = ?' if theme else ''}""",
            (kind, theme) if theme else (kind,),
        ).fetchone()["c"]
        # The static build browses the whole catalog client-side from
        # api/index.json — 23k rows can't be pre-rendered as pages.
        template = "sets_static.html" if STATIC_MODE else "sets.html"
        return templates.TemplateResponse(request, template, ctx(
            request, conn, items=items[:200], q=q, filter=filter, sort=sort,
            theme=theme, themes=themes, n_categories=n_categories,
            total=len(items), catalog_total=catalog_total,
            kind=kind, matched=matched,
        ))
    finally:
        conn.close()


def _maybe_auto_scan(conn, item_id):
    """Queue a scan for an item whose page was just opened and whose prices are
    missing or stale. Returns None, or {'state', 'position', 'age_days'} for
    the banner.

    Never runs during a static export: the exporter GETs every page through a
    test client, which would queue a scan for the entire catalog."""
    cfg = get_config()
    if STATIC_MODE or not cfg.auto_scan_on_view_days:
        return None

    last = dbq.last_scrape_any(conn, item_id)
    age_days = None
    if last:
        try:
            age_days = (datetime.now() - datetime.fromisoformat(last)).days
        except ValueError:
            age_days = None
        if age_days is not None and age_days < cfg.auto_scan_on_view_days:
            return None

    status = jobs.status()
    if status.get("current_item") == item_id:
        return {"state": "scanning", "position": 0, "age_days": age_days}
    position = jobs.enqueue(item_id)
    if position is None:
        # Already queued by an earlier view, or the queue is full.
        if item_id in status.get("queue", []):
            return {"state": "queued",
                    "position": status["queue"].index(item_id) + 1,
                    "age_days": age_days}
        return None
    return {"state": "scanning" if position == 1 and not status["running"] else "queued",
            "position": position, "age_days": age_days}


@app.get("/sets/{item_id}")
def set_detail(request: Request, item_id: str, parts_q: str = ""):
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        item_id = normalize_item_id(item_id)
        row = dbq.get_item(conn, item_id)
        if row is None:
            return RedirectResponse(f"/sets?q={item_id}", status_code=303)

        if row["item_type"] == "M" or (any(c.isalpha() for c in item_id)
                                       and not item_id[0].isdigit()):
            return _minifig_detail(request, conn, row)

        values = {}
        per_source_all = {}
        for condition in ("new", "used"):
            val, conf, ts = current_value(conn, item_id, condition)
            _, _, per_source = blend(conn, item_id, condition)
            for s, info in per_source.items():
                info = dict(info)
                info["display"] = disp(conn, info["native"], info["currency"], ccy)
                per_source_all.setdefault(s, {})[condition] = info
            values[condition] = {"value": disp(conn, val, "ILS", ccy),
                                 "confidence": conf, "as_of": ts,
                                 "velocity": velocity_mod.velocity(conn, item_id, condition)}

        retail_disp = disp(conn, row["retail_price"], row["retail_currency"] or "USD", ccy) \
            if row["retail_price"] else None
        # The MSRP as LEGO set it. Converting it into shekels invents a
        # price that never existed on any shelf.
        retail_native = row["retail_price"]
        retail_ccy = row["retail_currency"] or "USD"
        g_total, g_cagr = growth_mod.growth_vs_retail(conn, item_id)
        fc = forecast_mod.forecast(conn, item_id)
        if fc:
            for h in fc["horizons"].values():
                for k in ("value", "low", "high"):
                    h[k] = disp(conn, h[k], "ILS", ccy)
        ph = lifecycle.phase(row["year"], row["theme"])

        buy_target = values["new"]["value"] * 0.8 if values["new"]["value"] else None

        offers = cheapest_stock(conn, item_id)
        for s, o in offers.items():
            o["display"] = disp(conn, o["price"], o["currency"], ccy)
        best_source = min(offers, key=lambda s: offers[s]["display"] or 1e18) if offers else None

        inv = [{"id": r["fig_id"], "name": r["fig_name"], "qty": r["qty"]}
               for r in dbq.get_set_minifigs(conn, item_id)] or legacy_minifigs(item_id)
        # Both conditions, because a figure pulled out of a set is a used
        # figure — a sealed-new fig price is roughly double and is not what a
        # part-out actually fetches. Used leads; the page can switch to new.
        figs = []
        for f in inv:
            name = f.get("name")
            if not name:
                known = dbq.get_item(conn, f["id"])
                name = known["name"] if known else ""
            qty = f.get("qty") or 1
            fig = {**f, "name": name or ""}
            for condition in ("new", "used"):
                fv, _, _ = current_value(conn, f["id"], condition)
                v = disp(conn, fv, "ILS", ccy)
                fig[condition] = {"value": v, "total": (v or 0) * qty}
            figs.append(fig)

        # Each condition compares like with like: used figs against the used
        # set price, new against new. Mixing them (used figs over a sealed-set
        # price) halves the share and makes every set look fig-poor.
        figs_totals, figs_pcts, figs_priced = {}, {}, {}
        for condition in ("new", "used"):
            total = sum(f[condition]["total"] or 0 for f in figs)
            set_value = values[condition]["value"]
            figs_totals[condition] = total
            figs_pcts[condition] = (total / set_value * 100
                                    if total and set_value else None)
            # An unscanned fig counts as 0, which would quietly understate the
            # share rather than admit the number is not known yet.
            figs_priced[condition] = sum(1 for f in figs if f[condition]["value"])

        # Most valuable first, on the condition being led with, so the first
        # paint is already the useful order (matters for the static export and
        # with JS disabled).
        figs.sort(key=lambda f: f["used"]["total"] or 0, reverse=True)
        figs_partial = {c: bool(figs) and figs_priced[c] < len(figs)
                        for c in ("new", "used")}

        # Landed cost of buying this abroad and importing it: the BrickLink
        # average, plus Israeli VAT, plus a shipping allowance. Shipping is
        # quoted in USD because that is how couriers price it; the page lets
        # you change it and recomputes.
        cfg_now = get_config()
        ship_disp = disp(conn, cfg_now.import_shipping_usd, "USD", ccy) or 0.0
        import_cost = {"vat_pct": cfg_now.import_vat_pct,
                       "shipping_usd": cfg_now.import_shipping_usd,
                       "shipping": ship_disp, "conditions": {}}
        for condition in ("new", "used"):
            base = values[condition]["value"]
            if not base:
                continue
            vat = base * cfg_now.import_vat_pct / 100.0
            import_cost["conditions"][condition] = {
                "base": base, "vat": vat, "total": base + vat + ship_disp}

        parts = dbq.get_set_parts(conn, item_id, search=parts_q, limit=100)
        psum = dbq.parts_summary(conn, item_id)
        # Both conditions, so the figure/parts split can compare like with
        # like: used figure values against a used part-out total.
        povs = {}
        for condition in ("new", "used"):
            po = dbq.get_part_out(conn, item_id, condition)
            povs[condition] = disp(conn, po["pov_total"], po["currency"], ccy)                 if po else None
        pov = dbq.get_part_out(conn, item_id, "new")
        pov_disp = povs["new"]
        pov_premium = None
        if pov_disp and values["new"]["value"]:
            pov_premium = (pov_disp / values["new"]["value"] - 1) * 100.0

        # Split the part-out value into minifigures and everything else.
        # The POV is scraped with breakType=M, so minifigs are counted whole
        # and are already inside that total — the rest is the loose parts.
        #
        # Used leads, matching the figure table: a figure pulled out of a set
        # is a used figure, and a used part-out total is what it belongs
        # against. Falls back to new where no used POV has been scraped yet.
        splits = {}
        for condition in ("used", "new"):
            total, figs_total = povs[condition], figs_totals[condition]
            if not total or not figs_total:
                continue
            figs_share = min(figs_total, total)
            splits[condition] = {
                "figs": figs_share,
                "parts": max(0.0, total - figs_share),
                "total": total,
                "figs_pct": figs_share / total * 100.0,
                # The two numbers come from different BrickLink readings (our
                # own fig averages vs BrickLink's part-out calculator), so
                # they can disagree. Say so rather than draw a bogus slice.
                "over": figs_total > total,
                "partial": figs_partial[condition],
                "condition": condition,
            }
        split = splits.get("used") or splits.get("new")

        # sold/listing counts per source for the comparison table
        stats = {}
        for s in ("bricklink", "ebay", "brickowl"):
            sold = dbq.latest_snapshot(conn, item_id, s, "new", kind="sold")
            stock = dbq.latest_snapshot(conn, item_id, s, "new", kind="stock")
            stats[s] = {"sold": sold["listing_count"] if sold else None,
                        "stock": stock["listing_count"] if stock else None}

        # Sets from the same theme, closest by year — "collectors also track".
        related = []
        if row["theme"]:
            for other in conn.execute(
                    """SELECT item_id, name, year, item_type FROM items
                       WHERE theme = ? AND item_id != ? AND item_type = 'S'""",
                    (row["theme"], item_id)):
                ov, _, _ = current_value(conn, other["item_id"], "new")
                v = disp(conn, ov, "ILS", ccy)
                if not v:
                    continue
                og, _ = growth_mod.best_growth_estimate(conn, other["item_id"])
                related.append({"item_id": other["item_id"], "name": other["name"],
                                "year": other["year"], "value": v, "growth": og,
                                "gap": abs((other["year"] or 0) - (row["year"] or 0))})
            related.sort(key=lambda r: (r["gap"], -(r["value"] or 0)))
            related = related[:6]

        # Price per piece, and how it compares with the rest of the theme.
        ppp = ppp_theme_avg = None
        if row["parts"] and values["new"]["value"]:
            ppp = values["new"]["value"] / row["parts"]
            peers = []
            for other in conn.execute(
                    "SELECT item_id, parts FROM items WHERE theme = ? AND parts > 0",
                    (row["theme"],)):
                ov, _, _ = current_value(conn, other["item_id"], "new")
                v = disp(conn, ov, "ILS", ccy)
                if v:
                    peers.append(v / other["parts"])
            if len(peers) > 1:
                ppp_theme_avg = sum(peers) / len(peers)

        deal = deal_for(conn, item_id, ccy)
        # Whether it is already in the collection, so the buttons can say what
        # pressing them will do rather than offering "add" to something owned.
        held = conn.execute(
            "SELECT owned, wanted FROM portfolio WHERE item_id=?", (item_id,)
        ).fetchone()
        holding = {"owned": held["owned"] if held else 0,
                   "wanted": held["wanted"] if held else 0}

        return templates.TemplateResponse(request, "set_detail.html", ctx(
            request, conn, auto_scan=_maybe_auto_scan(conn, item_id),
            item=row, values=values, retail=retail_disp,
            retail_native=retail_native, retail_ccy=retail_ccy,
            growth_total=g_total, growth_cagr=g_cagr, forecast=fc, phase=ph,
            buy_target=buy_target, per_source=per_source_all, source_stats=stats,
            offers=offers, best_source=best_source,
            figs=figs, figs_totals=figs_totals, figs_pcts=figs_pcts,
            figs_priced=figs_priced, figs_partial=figs_partial,
            parts=parts, parts_summary=psum, parts_q=parts_q,
            pov=pov_disp, pov_premium=pov_premium,
            related=related, ppp=ppp, ppp_theme_avg=ppp_theme_avg, deal=deal,
            import_cost=import_cost, split=split, splits=splits,
            holding=holding,
            povs=povs,
        ))
    finally:
        conn.close()


def _minifig_detail(request: Request, conn, row):
    """Dedicated minifig page: values, history, and which sets contain it."""
    ccy = display_ccy(request)
    fig_id = row["item_id"]

    values = {}
    per_source_all = {}
    for condition in ("new", "used"):
        val, conf, ts = current_value(conn, fig_id, condition)
        _, _, per_source = blend(conn, fig_id, condition)
        for s, info in per_source.items():
            info = dict(info)
            info["display"] = disp(conn, info["native"], info["currency"], ccy)
            per_source_all.setdefault(s, {})[condition] = info
        values[condition] = {"value": disp(conn, val, "ILS", ccy),
                             "confidence": conf, "as_of": ts}

    g_total, g_cagr = growth_mod.growth_vs_retail(conn, fig_id)
    delta30 = dbq.market_delta(conn, fig_id, days=30)

    appears_in = []
    for s in dbq.sets_containing_fig(conn, fig_id):
        sv, _, _ = current_value(conn, s["set_id"], "new")
        appears_in.append({
            "set_id": s["set_id"], "name": s["name"] or "", "theme": s["theme"],
            "year": s["year"], "qty": s["qty"],
            "value": disp(conn, sv, "ILS", ccy),
        })

    offers = cheapest_stock(conn, fig_id)
    for s, o in offers.items():
        o["display"] = disp(conn, o["price"], o["currency"], ccy)
    best_source = min(offers, key=lambda s: offers[s]["display"] or 1e18) if offers else None

    return templates.TemplateResponse(request, "minifig_detail.html", ctx(
        request, conn, auto_scan=_maybe_auto_scan(conn, fig_id),
        item=row, values=values, per_source=per_source_all,
        growth_cagr=g_cagr, delta30=delta30, appears_in=appears_in,
        offers=offers, best_source=best_source,
    ))


@app.get("/set")
def set_lite(request: Request):
    """Client-rendered stand-in for catalog sets with no scraped prices.
    Only meaningful in the static export (`set.html?id=…`)."""
    conn = get_conn()
    try:
        return templates.TemplateResponse(request, "set_lite.html", ctx(request, conn))
    finally:
        conn.close()


@app.get("/api/index")
def search_index(request: Request):
    """Small catalog index powering the header's instant search. Exported as a
    static file too, so search keeps working on GitHub Pages."""
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        # Sets and figures only. A printed part shows on neither catalog tab,
        # but the header search walks this index without filtering by type, so
        # the eight statuettes typed 'P' were reachable from the search box
        # and linked to a page nothing else in the app leads to.
        rows = conn.execute(
            "SELECT item_id, name, theme, year, parts, item_type FROM items "
            "WHERE item_type IN ('S','M') ORDER BY item_id"
        ).fetchall()
        # `p` marks items that have their own page in the static export (only
        # price-scraped items get one — the catalog is far too big to render
        # 23k pages). Everything else opens the client-rendered set page.
        priced = {r["item_id"] for r in conn.execute(
            "SELECT DISTINCT item_id FROM price_snapshots")}
        # The values themselves, not just a "has been priced" flag. The
        # exported catalog renders from this file, so without them no listing
        # on the published site can show a price at all — which is exactly
        # what the minifig tab looked like.
        values = {}
        for cond, ix in (("new", 0), ("used", 1)):
            for r in conn.execute(
                    """SELECT s.item_id, s.market_price, s.currency
                       FROM price_snapshots s
                       JOIN (SELECT item_id, MAX(scraped_at) ts
                             FROM price_snapshots
                             WHERE source='blended' AND condition=? AND kind='market'
                             GROUP BY item_id) l
                         ON l.item_id = s.item_id AND l.ts = s.scraped_at
                       WHERE s.source='blended' AND s.condition=? AND s.kind='market'""",
                    (cond, cond)):
                if r["market_price"]:
                    v = disp(conn, r["market_price"], r["currency"], ccy)
                    values.setdefault(r["item_id"], [0, 0])[ix] = round(v or 0, 2)
        # Row arrays rather than objects: with a full catalog this file is
        # ~23k+ entries, and the compact form is roughly 40% smaller.
        themes = sorted({r["theme"] for r in rows if r["theme"]})
        theme_ix = {t: i for i, t in enumerate(themes)}
        return JSONResponse({
            "fields": ["id", "name", "theme", "year", "parts", "type", "p",
                       "vnew", "vused"],
            "currency": ccy,
            "themes": themes,
            "rows": [
                [r["item_id"], r["name"] or "", theme_ix.get(r["theme"], -1),
                 r["year"] or 0, r["parts"] or 0,
                 r["item_type"] or "S", 1 if r["item_id"] in priced else 0,
                 values.get(r["item_id"], (0, 0))[0],
                 values.get(r["item_id"], (0, 0))[1]]
                for r in rows
            ],
        })
    finally:
        conn.close()


@app.get("/compare")
def compare_page(request: Request):
    """Two to four items side by side.

    Rendered entirely on the client from /api/index and the per-item facts and
    history files, so the published site compares any pair exactly as the
    local app does. A server-rendered version could not be exported: the
    comparison is chosen at view time and there is no fixed set of pages to
    write.
    """
    conn = get_conn()
    try:
        return templates.TemplateResponse(request, "compare.html", ctx(
            request, conn, max_items=COMPARE_MAX))
    finally:
        conn.close()


@app.get("/api/sets/{item_id}/facts")
def facts_api(request: Request, item_id: str):
    """The comparable numbers for one item, in the display currency.

    /compare renders from this rather than from a server-built page, so the
    published site can compare any two items exactly as the local app does —
    the export writes one of these per scanned item, beside its history.
    """
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        item_id = normalize_item_id(item_id)
        row = dbq.get_item(conn, item_id)
        if not row:
            return JSONResponse({"error": "unknown item"}, status_code=404)

        values = {}
        for condition in ("new", "used"):
            val, conf, _ = current_value(conn, item_id, condition)
            values[condition] = {
                "value": disp(conn, val, "ILS", ccy),
                "confidence": conf,
                "velocity": velocity_mod.velocity(conn, item_id, condition),
            }

        growth, growth_basis = growth_mod.best_growth_estimate(conn, item_id)
        _, vs_retail = growth_mod.growth_vs_retail(conn, item_id)
        fc = forecast_mod.forecast(conn, item_id)
        horizon = None
        if fc and fc.get("horizons"):
            far = max(fc["horizons"], key=lambda k: fc["horizons"][k]["year"])
            h = fc["horizons"][far]
            horizon = {"year": h["year"],
                       "value": disp(conn, h["value"], "ILS", ccy)}

        pov = {}
        for condition in ("new", "used"):
            po = dbq.get_part_out(conn, item_id, condition=condition)
            pov[condition] = (disp(conn, po["pov_total"], po["currency"], ccy)
                              if po and po["pov_total"] else None)

        share = partout_mod.fig_shares(conn, "used", ccy).get(item_id)
        cheapest = None
        offers = (cheapest_stock(conn, item_id, "used")
                  or cheapest_stock(conn, item_id, "new"))
        if offers:
            src, best = min(offers.items(), key=lambda kv: kv[1]["price"])
            cheapest = {"source": src,
                        "price": disp(conn, best["price"], best["currency"], ccy)}

        parts = row["parts"] or 0
        value_new = values["new"]["value"]
        return JSONResponse({
            "id": item_id,
            "name": row["name"],
            "theme": row["theme"],
            "year": row["year"],
            "parts": parts or None,
            "minifigs": row["minifigs"] or None,
            "item_type": row["item_type"],
            "currency": ccy,
            "retail_native": row["retail_price"],
            "retail_ccy": row["retail_currency"] or "USD",
            "retail": disp(conn, row["retail_price"],
                           row["retail_currency"] or "USD", ccy)
                      if row["retail_price"] else None,
            "value_new": value_new,
            "value_used": values["used"]["value"],
            "confidence": values["new"]["confidence"] or values["used"]["confidence"],
            "ppp": round(value_new / parts, 3) if value_new and parts else None,
            "growth": growth,
            "growth_basis": growth_basis,
            "vs_retail": vs_retail,
            "forecast": horizon,
            "part_out_new": pov["new"],
            "part_out_used": pov["used"],
            "fig_share": share and {"pct": share["pct"], "figs": share["figs"],
                                    "priced": share["priced"],
                                    "part_out": share["part_out"]},
            "sales_6mo": (values["used"]["velocity"] or {}).get("sales")
                         or (values["new"]["velocity"] or {}).get("sales"),
            "cheapest": cheapest,
            "phase": lifecycle.phase(row["year"], row["theme"])["phase"],
        })
    finally:
        conn.close()


@app.get("/api/sets/{item_id}/history")
def history_api(request: Request, item_id: str, condition: str = "new"):
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        item_id = normalize_item_id(item_id)
        out = {"currency": ccy, "series": {}, "series_used": {}}

        def points(source, cond):
            return [
                {"t": ts.strftime("%Y-%m-%d"), "v": round(disp(conn, v, c, ccy) or 0, 2)}
                for ts, v, c in growth_mod.series(conn, item_id, condition=cond, source=source)
            ]

        for source in ("blended", "bricklink", "ebay", "brickowl"):
            out["series"][source] = points(source, condition)
        # Used prices ride along so the chart can show both conditions without
        # a second request (and so the static export stays a single file).
        out["series_used"]["blended"] = points("blended",
                                               "used" if condition == "new" else "new")
        fc = forecast_mod.forecast(conn, item_id, condition)
        if fc and out["series"]["blended"]:
            last = out["series"]["blended"][-1]
            fpts = [{"t": last["t"], "v": last["v"], "lo": last["v"], "hi": last["v"]}]
            for years, h in sorted(fc["horizons"].items()):
                fpts.append({"t": f"{h['year']}-12-31",
                             "v": round(disp(conn, h["value"], "ILS", ccy) or 0, 2),
                             "lo": round(disp(conn, h["low"], "ILS", ccy) or 0, 2),
                             "hi": round(disp(conn, h["high"], "ILS", ccy) or 0, 2)})
            out["forecast"] = fpts
        return JSONResponse(out)
    finally:
        conn.close()


# ── portfolio ────────────────────────────────────────────────────────────

@app.get("/portfolio")
def portfolio_page(request: Request, edit: str = None, imported: int = None,
                   skipped: int = None, removed: int = None):
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        rows = dbq.get_portfolio(conn)
        entries, total_value, total_paid, total_qty = [], 0.0, 0.0, 0
        # Built once per condition actually present, not once per row.
        fig_shares_by_condition = {}
        for row in rows:
            # Value each holding at the condition it is actually in. A used
            # set priced at sealed rates roughly doubles it, and most of a
            # real collection is used.
            # (fig_shares_by_condition is built at most twice, not per row.)
            condition = "used" if (row["condition"] or "new") == "used" else "new"
            # Not setdefault: it evaluates its second argument every time,
            # so the "cache" recomputed the whole lookup once per row — 92
            # scans of set_minifigs on a 92-set collection.
            if condition not in fig_shares_by_condition:
                fig_shares_by_condition[condition] = partout_mod.fig_shares(
                    conn, condition, ccy)
            fig_shares = fig_shares_by_condition[condition]
            val, _, _ = current_value(conn, row["item_id"], condition)
            v = disp(conn, val, "ILS", ccy)
            paid = disp(conn, row["purchase_price"], row["purchase_currency"] or "USD", ccy) \
                if row["purchase_price"] else None
            qty = row["owned"] or 1
            gain = ((v - paid) / paid * 100.0) if (v and paid) else None
            entries.append({
                "item_id": row["item_id"], "name": row["name"], "theme": row["theme"],
                "item_type": row["item_type"], "qty": qty,
                "paid": paid, "paid_raw": row["purchase_price"],
                "paid_ccy": row["purchase_currency"] or "USD",
                "purchase_date": row["purchase_date"], "condition": row["condition"],
                "value": v, "gain": gain,
                # The same condition the Value beside it uses. Defaulting to
                # new put a used holding's used value next to the change in
                # its sealed price — two different series read as one row.
                "delta30": dbq.market_delta(conn, row["item_id"],
                                            condition=condition, days=30),
                "signal": signals_mod.sell_signal(
                    conn, row["item_id"], paid=paid, condition=condition),
                # The app knows which sets are worth more in pieces and only
                # ever said so about sets on the market. It is a more useful
                # thing to know about one already on your shelf.
                "fig_share": fig_shares.get(row["item_id"]),
            })
            total_value += (v or 0) * qty
            total_paid += (paid or 0) * qty
            total_qty += qty
        total_gain = ((total_value - total_paid) / total_paid * 100.0) if total_paid else None

        # Wishlist: items marked "wanted" (the BrickEconomy CSV carries these).
        wishlist, wish_total = [], 0.0
        for row in conn.execute(
                """SELECT p.item_id, p.wanted, i.name, i.theme, i.year, i.item_type,
                          i.retail_price, i.retail_currency
                   FROM portfolio p JOIN items i ON i.item_id = p.item_id
                   WHERE p.wanted > 0 ORDER BY i.item_id"""):
            value, _, _ = current_value(conn, row["item_id"], "new")
            v = disp(conn, value, "ILS", ccy)
            deal = deal_for(conn, row["item_id"], ccy)
            wishlist.append({
                "item_id": row["item_id"], "name": row["name"], "theme": row["theme"],
                "item_type": row["item_type"], "wanted": row["wanted"], "value": v,
                "retail": disp(conn, row["retail_price"],
                               row["retail_currency"] or "USD", ccy) if row["retail_price"] else None,
                "retail_native": row["retail_price"],
                "retail_ccy": row["retail_currency"] or "USD",
                "delta30": dbq.market_delta(conn, row["item_id"], days=30),
                "deal": deal,
                "phase": lifecycle.phase(row["year"], row["theme"]),
            })
            wish_total += (v or 0) * (row["wanted"] or 1)

        return templates.TemplateResponse(request, "portfolio.html", ctx(
            request, conn, entries=entries, edit=edit,
            total_value=total_value, total_paid=total_paid,
            total_gain=total_gain, total_qty=total_qty,
            imported=imported, skipped=skipped, removed=removed,
            wishlist=wishlist, wish_total=wish_total,
        ))
    finally:
        conn.close()


@app.get("/api/portfolio/history")
def portfolio_history(request: Request):
    """Daily portfolio total: forward-filled sum of blended values."""
    conn = get_conn()
    try:
        ccy = display_ccy(request)
        rows = dbq.get_portfolio(conn)
        per_item = {}
        dates = set()
        for row in rows:
            qty = row["owned"] or 1
            pts = growth_mod.series(conn, row["item_id"],
                                    condition=row["condition"] or "new")
            if not pts:
                continue
            daily = {}
            for ts, v, c in pts:
                daily[ts.strftime("%Y-%m-%d")] = (disp(conn, v, c, ccy) or 0) * qty
            per_item[row["item_id"]] = daily
            dates.update(daily)

        # A set contributes nothing until it has been scanned once, so the
        # early part of this series measured how much of the collection had
        # been scanned, not what it was worth: 45 of 84 sets were known on the
        # first date, and the line climbed as the other 39 arrived. That reads
        # as a portfolio appreciating when it is only a scanner catching up.
        # Start where the collection is substantially covered instead.
        total = len(per_item)
        full, last_known = [], {}
        for d in sorted(dates):
            for iid, daily in per_item.items():
                if d in daily:
                    last_known[iid] = daily[d]
            full.append({"t": d, "v": round(sum(last_known.values()), 2),
                         "n": len(last_known)})

        # Trim the leading ramp, where the total is low because sets had not
        # been scanned rather than because they were worth less.
        #
        # The threshold is a share of the coverage finally reached, and that
        # figure grows: a scan that discovers four more sets pushes every
        # earlier day below it. Left unbounded this collapsed 226 days of
        # history to a single point the first time coverage moved. So it keeps
        # a floor of days whatever the coverage, and says when those early
        # days are thin instead of hiding them.
        cut = 0
        while (cut < len(full) - PORTFOLIO_CHART_MIN_POINTS
               and total and full[cut]["n"] < total * PORTFOLIO_CHART_COVERAGE):
            cut += 1
        series = full[cut:]
        thin = [p for p in series if total and p["n"] < total * PORTFOLIO_CHART_COVERAGE]
        return JSONResponse({"currency": ccy, "series": series,
                             "tracked": total,
                             "covered_from": series[0]["t"] if series else None,
                             # Days kept only to leave a readable line, whose
                             # total covers fewer sets than today's.
                             "thin_points": len(thin),
                             "min_covered": min((p["n"] for p in series),
                                                default=0)})
    finally:
        conn.close()


@app.post("/portfolio/add")
def portfolio_add(request: Request, item_id: str = Form(...),
                  qty: int = Form(1), condition: str = Form("new"),
                  want: bool = Form(False), back: str = Form("")):
    """Add to the collection, or to the wishlist.

    `back` returns to the page the button was pressed on, so adding from a set
    page does not throw you out to the portfolio and lose your place.
    """
    conn = get_conn()
    try:
        iid = normalize_item_id(item_id.strip())
        if iid:
            dbq.upsert_item(conn, iid, item_type=item_type_for(iid))
            existing = conn.execute(
                "SELECT owned, wanted FROM portfolio WHERE item_id=?", (iid,)
            ).fetchone()
            owned = existing["owned"] if existing else 0
            wanted = existing["wanted"] if existing else 0
            if want:
                # Wanting something already owned is a second copy wanted, not
                # a contradiction — leave the holding alone.
                dbq.upsert_portfolio(conn, iid, owned=owned, wanted=wanted + 1,
                                     condition=condition)
            else:
                dbq.upsert_portfolio(conn, iid, owned=owned + max(1, qty),
                                     wanted=wanted, condition=condition)
        # Only ever back to a path on this site, never an absolute URL from
        # the form: that would make this an open redirect.
        target = back if back.startswith("/") and not back.startswith("//")             else "/portfolio"
        return RedirectResponse(f"{target}?added={iid}", status_code=303)
    finally:
        conn.close()


# ── portfolio import ─────────────────────────────────────────────────────

def parse_import_file(filename: str, content: bytes):
    """Returns rows [{item_id, name, theme, year, qty, retail, retail_ccy}]."""
    text = content.decode("utf-8-sig", errors="replace")
    rows = []

    if filename.lower().endswith(".xml") or text.lstrip().startswith("<"):
        # BrickLink wanted-list XML
        root = ET.fromstring(text)
        for el in root.iter("ITEM"):
            iid = (el.findtext("ITEMID") or "").strip()
            if not iid:
                continue
            qty = int(el.findtext("MINQTY") or el.findtext("QTY") or 1)
            rows.append({"item_id": normalize_item_id(iid), "name": None,
                         "theme": None, "year": None, "qty": max(1, qty),
                         "retail": None, "retail_ccy": None,
                         "paid": None, "paid_ccy": None, "condition": "new"})
        return rows

    sniff = text.splitlines()[0] if text.splitlines() else ""
    if "," in sniff and "number" in sniff.lower():
        # BrickEconomy CSV export
        for row in csv.DictReader(io.StringIO(text)):
            number = (row.get("Number") or "").strip()
            if not number:
                continue
            retail, retail_ccy = parse_money(row.get("Retail", ""))
            paid, paid_ccy = parse_money(row.get("Paid", ""))
            year = row.get("Year", "").strip()
            rows.append({
                "item_id": normalize_item_id(number),
                # BrickEconomy renamed this column between export versions.
                "name": ((row.get("SetName") or row.get("Name") or "").strip()
                         or None),
                "theme": (row.get("Theme") or "").strip() or None,
                "year": int(year) if year.isdigit() else None,
                "qty": max(1, int(row.get("Owned") or 1)),
                "retail": retail, "retail_ccy": retail_ccy,
                # What it cost and what shape it is in — newer exports carry
                # both, and the valuation depends on the condition.
                "paid": paid, "paid_ccy": paid_ccy,
                "condition": parse_condition(row.get("Condition", "")),
            })
        return rows

    # Plain set list: one id per line
    for line in text.splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if token and re.match(r"^[A-Za-z0-9-]+$", token):
            rows.append({"item_id": normalize_item_id(token), "name": None,
                         "theme": None, "year": None, "qty": 1,
                         "retail": None, "retail_ccy": None,
                         "paid": None, "paid_ccy": None, "condition": "new"})
    return rows


@app.post("/portfolio/import")
async def portfolio_import(request: Request, file: UploadFile):
    conn = get_conn()
    try:
        content = await file.read()
        try:
            rows = parse_import_file(file.filename or "upload", content)
        except ET.ParseError as exc:
            rows, parse_error = [], str(exc)
        else:
            parse_error = None

        preview = []
        for r in rows:
            existing = conn.execute(
                "SELECT owned FROM portfolio WHERE item_id=?", (r["item_id"],)
            ).fetchone()
            known = dbq.get_item(conn, r["item_id"])
            preview.append({**r,
                            "status": "merge" if existing else ("known" if known else "new"),
                            "existing_qty": existing["owned"] if existing else 0})
        # What this file implies has been sold, so the preview can offer to
        # clear it out instead of leaving stale holdings behind forever.
        ids = {r["item_id"] for r in rows}
        types = tuple({item_type_for(i) for i in ids}) or ("S",)
        sold = sold_off(conn, ids, item_types=types) if ids else []
        return templates.TemplateResponse(request, "import_preview.html", ctx(
            request, conn, rows=preview, filename=file.filename,
            payload=json.dumps(rows), parse_error=parse_error, sold=sold,
        ))
    finally:
        conn.close()


@app.post("/portfolio/import/confirm")
def portfolio_import_confirm(request: Request, payload: str = Form(...),
                             prune: bool = Form(False)):
    conn = get_conn()
    try:
        rows = json.loads(payload)
        imported = skipped = 0
        seen = set()
        for r in rows:
            iid = normalize_item_id(str(r.get("item_id", "")).strip())
            if not iid:
                skipped += 1
                continue
            dbq.upsert_item(conn, iid, item_type=item_type_for(iid),
                            name=r.get("name"), theme=r.get("theme"),
                            year=r.get("year"), retail_price=r.get("retail"),
                            retail_currency=r.get("retail_ccy"))
            # Prefer what was paid; fall back to retail for the older exports
            # and the plain-list formats that carry no price at all.
            paid = r.get("paid")
            dbq.upsert_portfolio(
                conn, iid, owned=max(1, int(r.get("qty") or 1)),
                purchase_price=paid if paid is not None else r.get("retail"),
                purchase_currency=(r.get("paid_ccy") or r.get("retail_ccy")
                                   or "USD"),
                condition=r.get("condition") or "new",
                # First sighting replaces the stored row, so re-importing the
                # same file is idempotent; a repeat within one file is a
                # second copy owned and adds to the quantity.
                merge_qty=iid in seen,
            )
            seen.add(iid)
            imported += 1

        removed = 0
        if prune and seen:
            types = tuple({item_type_for(i) for i in seen})
            for row in sold_off(conn, seen, item_types=types):
                dbq.delete_portfolio(conn, row["item_id"])
                removed += 1
        return RedirectResponse(
            f"/portfolio?imported={imported}&skipped={skipped}&removed={removed}",
            status_code=303)
    finally:
        conn.close()


# NOTE: registered AFTER every literal /portfolio/... route. FastAPI
# matches in registration order, so declaring /portfolio/{item_id} first
# made it swallow POST /portfolio/import: the upload 422d on the edit
# form's required `qty` and the import flow could never start.

@app.post("/portfolio/{item_id}")
def portfolio_edit(request: Request, item_id: str,
                   qty: int = Form(...), condition: str = Form("new"),
                   paid: str = Form(""), paid_ccy: str = Form("USD"),
                   purchase_date: str = Form("")):
    conn = get_conn()
    try:
        price, _ = parse_money(paid)
        if qty <= 0:
            dbq.delete_portfolio(conn, item_id)
        else:
            dbq.upsert_portfolio(conn, item_id, owned=qty, condition=condition,
                                 purchase_price=price,
                                 purchase_currency=paid_ccy if paid_ccy in SUPPORTED_CURRENCIES else "USD",
                                 purchase_date=purchase_date or None)
        return RedirectResponse("/portfolio", status_code=303)
    finally:
        conn.close()


@app.post("/portfolio/{item_id}/delete")
def portfolio_delete(request: Request, item_id: str):
    conn = get_conn()
    try:
        dbq.delete_portfolio(conn, item_id)
        return RedirectResponse("/portfolio", status_code=303)
    finally:
        conn.close()


# ── refresh & settings ───────────────────────────────────────────────────

@app.get("/refresh")
def refresh_page(request: Request):
    conn = get_conn()
    try:
        cfg = get_config()
        sources_health = []
        for source in ("bricklink", "ebay", "brickowl"):
            last = conn.execute(
                "SELECT MAX(scraped_at) ts, COUNT(DISTINCT item_id) n FROM price_snapshots "
                "WHERE source=? AND scraped_at > datetime('now', '-1 day')",
                (source,),
            ).fetchone()
            last_ever = conn.execute(
                "SELECT MAX(scraped_at) ts FROM price_snapshots WHERE source=?",
                (source,),
            ).fetchone()
            sources_health.append({"source": source, "items_24h": last["n"],
                                   "last_scan": last_ever["ts"]})
        n_portfolio = conn.execute("SELECT COUNT(*) c FROM portfolio WHERE owned > 0").fetchone()["c"]
        n_items = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        coverage = dbq.theme_coverage(conn, ttl_days=cfg.auto_scan_on_view_days or 30)
        covered = sum(t["scanned"] for t in coverage)
        fresh = sum(t["fresh"] for t in coverage)
        from ..scrapers.ebay import EbaySource
        return templates.TemplateResponse(request, "refresh.html", ctx(
            request, conn, cfg=cfg, sources_health=sources_health,
            n_portfolio=n_portfolio, n_items=n_items, coverage=coverage,
            covered=covered, fresh=fresh,
            ebay_signed_in=bool(EbaySource.signed_in_profile()),
            # From the lock, not from this process's job state: the nightly
            # task and any run started from a terminal are invisible to the
            # latter, and the page was offering an enabled Start scan button
            # while a scan was underway.
            scan_elsewhere=refresh_mod.scan_in_progress() and not jobs.status()["running"],
        ))
    finally:
        conn.close()


@app.post("/refresh")
def refresh_start(request: Request, scope: str = Form("portfolio"),
                  item_id: str = Form(""), force: bool = Form(False),
                  theme: str = Form(""), inventory_only: bool = Form(False),
                  with_figs: bool = Form(False)):
    theme = theme.strip()
    if theme and scope not in ("theme",):
        scope = "theme"          # picking a theme is the intent, whatever the select says
    started = jobs.start(scope=scope, item_id=item_id.strip() or None,
                         force=force, theme=theme or None,
                         inventory_only=inventory_only, with_figs=with_figs)
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse(jobs.status(), status_code=200 if started else 409)
    return RedirectResponse("/refresh", status_code=303)


@app.post("/refresh/action")
def refresh_action(request: Request, action: str = Form(...)):
    """Catalog import / static export / eBay session check, so every scraping
    job is drivable from the site and not only from the command line."""
    if action in jobs.ACTIONS:
        jobs.start_action(action)
    return RedirectResponse("/refresh", status_code=303)


@app.get("/api/refresh/status")
def refresh_status():
    return JSONResponse(jobs.status())


@app.post("/settings/currency")
def set_currency(request: Request, ccy: str = Form(...)):
    resp = RedirectResponse(request.headers.get("referer", "/"), status_code=303)
    if ccy.upper() in SUPPORTED_CURRENCIES:
        resp.set_cookie("ccy", ccy.upper(), max_age=365 * 24 * 3600)
    return resp
