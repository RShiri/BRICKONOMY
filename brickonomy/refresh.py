"""Refresh orchestrator — scrape enabled sources, write snapshots, blend.

  python -m brickonomy.refresh --item 75192
  python -m brickonomy.refresh --scope portfolio
  python -m brickonomy.refresh --scope stale
  python -m brickonomy.refresh --theme "Super Heroes Marvel"
  python -m brickonomy.refresh --scope all

Per item and source: skip if a snapshot newer than scrape_ttl_days exists
(unless --force), otherwise fetch → PriceAnalyzer → snapshot rows. After the
sources, the blended value is computed and stored. For sets, the BrickLink
parts inventory and part-out value are refreshed when older than 30 days.

Used by both the CLI and the web app's background job (via run_refresh with a
progress callback).
"""
import argparse
from datetime import datetime, timedelta

from . import db as dbq
from .analytics.valuation import store_blended
from .config import get_config
from .importer import item_type_for, normalize_item_id
from .scrapers import SOURCES
from .scrapers.base import polite_sleep

PARTS_TTL_DAYS = 30


def _update_item_meta(conn, item_id, meta):
    if not meta:
        return
    specs = meta.get("specs", {})
    name = meta.get("item_name")
    # Keep an already-known name (e.g. from the BrickEconomy CSV) instead of
    # letting each marketplace's own title churn it.
    existing = dbq.get_item(conn, item_id)
    if existing and existing["name"] and not existing["name"].startswith("Set "):
        name = None
    dbq.upsert_item(
        conn, item_id,
        name=name if name and name != "Unknown" else None,
        year=meta.get("year_released"),
        parts=specs.get("parts") or None,
        minifigs=specs.get("minifigs") or None,
        weight_g=specs.get("weight_g") or None,
    )


def refresh_item(conn, item_id, item_type=None, force=False, log=print,
                 inventory_only=False):
    """Refresh one item across all enabled sources. Returns
    {source: 'ok'|'fresh'|'empty'|error-string}.

    inventory_only skips the marketplace price scrape and only fetches the
    set's parts + minifig inventory from BrickLink."""
    from .snapshots import write_snapshots

    cfg = get_config()
    item_id = normalize_item_id(item_id)
    item_type = item_type or item_type_for(item_id)
    results = {}

    for idx, source_name in enumerate([] if inventory_only else cfg.sources_enabled):
        if source_name not in SOURCES:
            results[source_name] = "unknown source"
            continue
        if not force and dbq.is_fresh(conn, item_id, source_name, cfg.scrape_ttl_days):
            results[source_name] = "fresh"
            continue

        scraper = SOURCES[source_name]()
        if idx > 0 and not cfg.fixture_mode:
            polite_sleep()
        res = scraper.fetch(item_id, item_type)

        if res.error:
            results[source_name] = res.error
            log(f"  ✘ {source_name}: {res.error}")
            continue
        if res.is_empty:
            results[source_name] = "empty"
            log(f"  ○ {source_name}: no listings found")
            continue

        _update_item_meta(conn, item_id, res.meta)
        from .snapshots import minifig_floor_values
        write_snapshots(conn, item_id, source_name, res.currency,
                        res.analyzer_input(), keep_raw=True,
                        minifig_values=minifig_floor_values(conn, item_id, res.currency))
        n = (len(res.new["sold"]) + len(res.used["sold"])
             + len(res.new["stock"]) + len(res.used["stock"]))
        results[source_name] = "ok"
        log(f"  ✔ {source_name}: {n} listings ({res.currency})")

    blended = None if inventory_only else store_blended(conn, item_id)
    if blended:
        log(f"  ≈ blended: " + ", ".join(f"{c} {v:,.0f}" for c, v in blended.items()))

    if item_type == "S" and "bricklink" in cfg.sources_enabled and not cfg.fixture_mode:
        _refresh_parts(conn, item_id, force=force, log=log)
        _refresh_minifigs(conn, item_id, force=force, log=log)

    return results


def _refresh_minifigs(conn, set_id, force=False, log=print):
    """Store the set's minifig inventory (with real quantities) once, refresh
    only on --force; the fig list of a released set doesn't change."""
    from .scrapers.bricklink import BrickLinkSource

    if not force and dbq.get_set_minifigs(conn, set_id):
        return
    figs, err = BrickLinkSource().fetch_minifig_inventory(set_id)
    if figs:
        dbq.upsert_set_minifigs(conn, set_id, figs)
        for f in figs:  # figs become first-class items with their own pages
            dbq.upsert_item(conn, f["id"], item_type="M", name=f["name"] or None)
        log(f"  ⚙ minifig inventory: {len(figs)} figs")
    elif err:
        log(f"  ✘ minifig inventory: {err}")


def _refresh_parts(conn, set_id, force=False, log=print):
    from .scrapers.bricklink import BrickLinkSource

    summary = dbq.parts_summary(conn, set_id)
    pov = dbq.get_part_out(conn, set_id)
    stale_cutoff = datetime.now() - timedelta(days=PARTS_TTL_DAYS)
    pov_fresh = False
    if pov:
        try:
            pov_fresh = datetime.fromisoformat(pov["scraped_at"]) >= stale_cutoff
        except (TypeError, ValueError):
            pass
    if not force and summary["lots"] > 0 and pov_fresh:
        return

    src = BrickLinkSource()
    if summary["lots"] == 0 or force:
        parts, err = src.fetch_parts_inventory(set_id)
        if parts:
            dbq.upsert_set_parts(conn, set_id, parts)
            log(f"  ⚙ parts inventory: {len(parts)} lots")
        elif err:
            log(f"  ✘ parts inventory: {err}")
    if not pov_fresh or force:
        value, pov_ccy, err = src.fetch_part_out_value(set_id)
        if value is not None:
            # The POV page is fetched without a session, so it is in the site
            # default currency, not the scraper's — store what it actually is.
            dbq.upsert_part_out(conn, set_id, value, pov_ccy or "USD")
            log(f"  ⚙ part-out value: {value:,.2f} {pov_ccy or 'USD'}")
        elif err:
            log(f"  ✘ part-out value: {err}")


def _stale_items(conn, ttl_days):
    """Item ids where at least one enabled source has no fresh snapshot."""
    cfg = get_config()
    stale = []
    for row in conn.execute("SELECT item_id, item_type FROM items"):
        for source in cfg.sources_enabled:
            if not dbq.is_fresh(conn, row["item_id"], source, ttl_days):
                stale.append((row["item_id"], row["item_type"]))
                break
    return stale


def run_refresh(scope="portfolio", item_id=None, force=False, theme=None,
                limit=None, progress=None, log=print, inventory_only=False):
    """Entry point shared by the CLI and the web background job.

    progress: optional callback(done, total, current_item, errors: list).
    Returns {'done': n, 'errors': [(item, source, error), ...]}."""
    cfg = get_config()
    conn = dbq.connect()
    try:
        if item_id:
            targets = [(normalize_item_id(item_id), None)]
        elif scope == "portfolio":
            targets = [(r["item_id"], r["item_type"]) for r in dbq.get_portfolio(conn)]
        elif scope == "stale":
            targets = _stale_items(conn, cfg.scrape_ttl_days)
        elif scope == "theme":
            if not theme:
                raise ValueError("scope 'theme' needs a theme name")
            # Sets first, then the theme's minifigs; never-scanned before
            # stale, so an interrupted run made progress where it mattered.
            #
            # The minifig half needs the set_minifigs join: figs imported from
            # the catalog have no theme of their own, so `theme = ?` alone
            # would scan a theme's sets and silently skip every fig in them.
            targets = [(r["item_id"], r["item_type"]) for r in conn.execute(
                """SELECT i.item_id, i.item_type FROM items i
                   LEFT JOIN (SELECT item_id, MAX(scraped_at) ts
                              FROM price_snapshots GROUP BY item_id) s
                     ON s.item_id = i.item_id
                   WHERE i.theme = ?
                      OR i.item_id IN (
                           SELECT sm.fig_id FROM set_minifigs sm
                           JOIN items si ON si.item_id = sm.set_id
                           WHERE si.theme = ?)
                   ORDER BY i.item_type DESC, s.ts IS NOT NULL, s.ts""",
                (theme, theme))]
        elif scope == "gaps":
            # Whatever the catalog is still missing, most-useful first: sets
            # before minifigs, never-scanned before merely stale. Feeding this
            # a --limit repeatedly walks the whole catalog over many runs.
            targets = [(r["item_id"], r["item_type"]) for r in conn.execute(
                """SELECT i.item_id, i.item_type FROM items i
                   LEFT JOIN (SELECT item_id, MAX(scraped_at) ts
                              FROM price_snapshots GROUP BY item_id) s
                     ON s.item_id = i.item_id
                   ORDER BY i.item_type DESC, s.ts IS NOT NULL, s.ts""")]
        elif scope == "all":
            targets = [(r["item_id"], r["item_type"])
                       for r in conn.execute("SELECT item_id, item_type FROM items")]
        else:
            raise ValueError(f"unknown scope {scope!r}")

        if limit and len(targets) > limit:
            # Targets are ordered never-scanned first, so a limited run always
            # takes the most useful slice. Say what was left out.
            log(f"… {len(targets)} items match; scanning the first {limit}, "
                f"{len(targets) - limit} left for the next run")
            targets = targets[:limit]

        errors = []
        total = len(targets)
        for i, (iid, itype) in enumerate(targets):
            log(f"[{i + 1}/{total}] {iid}")
            if progress:
                progress(i, total, iid, errors)
            results = refresh_item(conn, iid, itype, force=force, log=log,
                                   inventory_only=inventory_only)
            for source, status in results.items():
                if status not in ("ok", "fresh", "empty"):
                    errors.append((iid, source, status))
            if i + 1 < total and not cfg.fixture_mode:
                polite_sleep()
        if progress:
            progress(total, total, None, errors)
        return {"done": total, "errors": errors}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Refresh prices across sources")
    ap.add_argument("--item", help="single item id, e.g. 75192 or sw0636")
    ap.add_argument("--scope", default="portfolio",
                    choices=["portfolio", "stale", "theme", "gaps", "all"])
    ap.add_argument("--theme", help="theme name for --scope theme, "
                                    "e.g. \"Super Heroes Marvel\"")
    ap.add_argument("--force", action="store_true",
                    help="ignore the freshness TTL")
    ap.add_argument("--limit", type=int,
                    help="scan at most N items (never-scanned ones first)")
    ap.add_argument("--inventory-only", action="store_true",
                    help="only fetch parts + minifig inventory, no price scrape")
    args = ap.parse_args()

    if args.theme and args.scope == "portfolio":
        args.scope = "theme"          # --theme alone implies --scope theme

    summary = run_refresh(scope=args.scope, item_id=args.item,
                          force=args.force, theme=args.theme, limit=args.limit,
                          inventory_only=args.inventory_only)
    print(f"\nRefreshed {summary['done']} item(s); {len(summary['errors'])} source error(s).")
    for iid, source, err in summary["errors"][:20]:
        print(f"  {iid} · {source}: {err}")


if __name__ == "__main__":
    main()
