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
import contextlib
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import db as dbq
from .analytics.valuation import store_blended
from .compat import REPO_ROOT
from .config import get_config
from .importer import item_type_for, normalize_item_id
from .scrapers import SOURCES
from .scrapers.base import polite_sleep

PARTS_TTL_DAYS = 30
LOCK_PATH = REPO_ROOT / ".scan.lock"

# Tier labels for --scope priority, in the order the SQL assigns them.
PRIORITY_TIERS = {
    1: "owned",
    2: "wishlist",
    3: "fig in owned set",
    4: "held theme",
    5: "catalog",
}


# Windows byte-range locks apply to the locked bytes themselves, so the lock
# is taken far past any content the file will ever hold — otherwise the owner
# cannot write its own pid line without colliding with its own lock.
_LOCK_OFFSET = 1 << 30


def _try_lock(handle):
    """Take an exclusive OS lock on an open file, or return False."""
    try:
        import msvcrt                                   # Windows
    except ImportError:
        import fcntl                                    # POSIX
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def scan_lock(path=None):
    """Yield True if this process took the scan lock, False if one is held.

    A nightly task and a hand-run scan must not overlap: they would interleave
    writes to the same SQLite file and hit the same sites twice as fast from a
    single address. This is an OS-level file lock rather than a pid written to
    a file, so a killed run releases it automatically and there is no stale
    lock to clean up — and notably no pid liveness check, which on Windows
    would mean os.kill(pid, 0), and os.kill there *terminates* the process.
    """
    path = Path(path or LOCK_PATH)
    handle = open(path, "a+", encoding="utf-8")
    try:
        if not _try_lock(handle):
            yield False
            return
        # Informational only — the lock is the OS's, not this line's.
        handle.seek(0)
        handle.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n")
        handle.flush()
        yield True
    finally:
        handle.close()
        # Best-effort tidy-up; the lock itself was released by closing.
        with contextlib.suppress(OSError):
            path.unlink()


def plan_targets(scope="portfolio", item_id=None, theme=None, limit=None):
    """[(item_id, item_type, why), ...] — what a run would scan, in order."""
    conn = dbq.connect()
    try:
        targets = select_targets(conn, scope, item_id, theme)
        if limit:
            targets = targets[:limit]
        if scope != "priority":
            return [(i, t, scope) for i, t, *_ in targets]
        tiers = {r["item_id"]: r["tier"] for r in conn.execute(
            """SELECT i.item_id,
                      CASE WHEN p.owned > 0 THEN 1
                           WHEN p.wanted > 0 THEN 2
                           WHEN i.item_id IN (SELECT sm.fig_id FROM set_minifigs sm
                                              JOIN portfolio op ON op.item_id = sm.set_id
                                              WHERE op.owned > 0) THEN 3
                           WHEN i.theme IN (SELECT DISTINCT ti.theme FROM items ti
                                            JOIN portfolio tp ON tp.item_id = ti.item_id
                                            WHERE tp.owned > 0 AND ti.theme IS NOT NULL) THEN 4
                           ELSE 5 END AS tier
               FROM items i LEFT JOIN portfolio p ON p.item_id = i.item_id""")}
        return [(i, t, PRIORITY_TIERS.get(tiers.get(i, 5), "catalog"))
                for i, t, *_ in targets]
    finally:
        conn.close()


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
    # An inventory where nothing is named came from the old parser, which
    # stored each part's number as its name and so never found a colour
    # either. It is not a usable inventory, and because the refresh skips any
    # set that already has lots, it would have stayed that way forever.
    unnamed = summary["lots"] > 0 and not summary["named"]
    if not force and summary["lots"] > 0 and pov_fresh and not unnamed:
        return

    src = BrickLinkSource()
    if summary["lots"] == 0 or unnamed or force:
        if unnamed:
            log(f"  ↻ parts inventory has no names — refetching")
        parts, err = src.fetch_parts_inventory(set_id)
        if parts:
            dbq.upsert_set_parts(conn, set_id, parts)
            log(f"  ⚙ parts inventory: {len(parts)} lots")
        elif err:
            log(f"  ✘ parts inventory: {err}")
    if not pov_fresh or force:
        # Both conditions: BrickLink prices a sealed break-up and a used one
        # differently, and comparing used figure values against a new part-out
        # total mixes two bases. One extra request per set.
        for condition, flag in (("new", "N"), ("used", "U")):
            value, pov_ccy, err = src.fetch_part_out_value(set_id, condition=flag)
            if value is not None:
                # The POV page is fetched without a session, so it is in the
                # site default currency, not the scraper's — store what it is.
                dbq.upsert_part_out(conn, set_id, value, pov_ccy or "USD",
                                    condition=condition)
                log(f"  ⚙ part-out value ({condition}): {value:,.2f} {pov_ccy or 'USD'}")
            elif err:
                log(f"  ✘ part-out value ({condition}): {err}")
            if not get_config().fixture_mode:
                polite_sleep()


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


def select_targets(conn, scope="portfolio", item_id=None, theme=None):
    """The ordered work list for a scope. Shared by the real run and
    --dry-run, so what gets previewed is exactly what gets scanned."""
    cfg = get_config()
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
    elif scope == "inventories":
        # Sets whose stored inventory is missing or unusable. Both halves are
        # fetched once and then skipped forever — a released set's parts and
        # figure lists do not change — so a set scanned while either parser
        # was broken kept the damage permanently and nothing would go back
        # for it.
        #
        #   no fig list      314 Marvel sets had a part-out value and no
        #                    figures, the Daily Bugle's 25 among them
        #   unnamed parts    every part stored under its own number as its
        #                    name, which also left every colour empty
        #
        # Pair with --inventory-only to skip the price scrape.
        # Biggest sets first: those are the fig-heavy ones, and the figure
        # share of value matters most where there are figures to share it.
        targets = [(r["item_id"], "S") for r in conn.execute(
            """SELECT i.item_id FROM items i
               LEFT JOIN (SELECT DISTINCT set_id FROM set_minifigs) sm
                 ON sm.set_id = i.item_id
               LEFT JOIN (SELECT set_id,
                                 SUM(CASE WHEN part_name IS NOT NULL
                                           AND part_name != ''
                                           AND part_name != part_no
                                          THEN 1 ELSE 0 END) AS named
                          FROM set_parts GROUP BY set_id) sp
                 ON sp.set_id = i.item_id
               WHERE i.item_type = 'S'
                 AND (sm.set_id IS NULL OR COALESCE(sp.named, 0) = 0)
                 AND i.item_id IN (SELECT DISTINCT item_id FROM price_snapshots)
               ORDER BY COALESCE(i.parts, 0) DESC""")]
    elif scope == "priority":
        # What an unattended nightly run should spend its budget on, in
        # the order it matters. `gaps` walks the catalog by id and would
        # spend weeks on sets nobody here owns before reaching the figures
        # sitting inside the collection.
        #
        #   1 owned          the portfolio itself
        #   2 wanted         the wishlist
        #   3 owned figs     figures inside owned sets — the biggest hole,
        #                    since scanning a set never priced its figures
        #   4 held themes    other sets in themes already collected
        #   5 everything     the rest of the catalog
        #
        # Within a tier: never-scanned first, then oldest scan first, so an
        # interrupted run always made progress where it counted.
        targets = [(r["item_id"], r["item_type"]) for r in conn.execute(
            """SELECT i.item_id, i.item_type,
                      CASE
                        WHEN p.owned > 0 THEN 1
                        WHEN p.wanted > 0 THEN 2
                        WHEN i.item_id IN (
                             SELECT sm.fig_id FROM set_minifigs sm
                             JOIN portfolio op ON op.item_id = sm.set_id
                             WHERE op.owned > 0) THEN 3
                        WHEN i.theme IN (
                             SELECT DISTINCT ti.theme FROM items ti
                             JOIN portfolio tp ON tp.item_id = ti.item_id
                             WHERE tp.owned > 0 AND ti.theme IS NOT NULL) THEN 4
                        ELSE 5
                      END AS tier
               FROM items i
               LEFT JOIN portfolio p ON p.item_id = i.item_id
               LEFT JOIN (SELECT item_id, MAX(scraped_at) ts
                          FROM price_snapshots GROUP BY item_id) s
                 ON s.item_id = i.item_id
               ORDER BY tier, s.ts IS NOT NULL, s.ts, i.item_id""")]
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
    return targets


def unpriced_figs_of(conn, set_id):
    """Figures in a set that have never been priced, most-contained first.

    A set scan discovers its inventory, but discovering a figure is not the
    same as pricing it — that needs a scan of the figure's own page.
    """
    return [r["fig_id"] for r in conn.execute(
        """SELECT sm.fig_id FROM set_minifigs sm
           WHERE sm.set_id = ?
             AND sm.fig_id NOT IN (SELECT DISTINCT item_id FROM price_snapshots)
           ORDER BY sm.qty DESC, sm.fig_id""", (set_id,))]


def run_refresh(scope="portfolio", item_id=None, force=False, theme=None,
                limit=None, progress=None, log=print, inventory_only=False,
                with_figs=None):
    """Entry point shared by the CLI and the web background job.

    with_figs: after scanning a set, price the figures it turns out to
    contain, in the same run. Defaults on for the priority scope — a set's
    inventory is only discovered *during* its scan, so without this every
    newly-found figure waits for the next night, and a set scanned today
    contributes nothing to the fig-value totals until tomorrow.

    progress: optional callback(done, total, current_item, errors: list).
    Returns {'done': n, 'errors': [...]}, or {'blocked': True} when another
    scan already holds the lock.

    The lock is taken here rather than at the CLI entry point, because that is
    not the only entry point: viewing a stale set queues a scan through the
    web worker, which called this directly and so ran straight through a
    nightly run — two processes interleaving writes to the same SQLite file
    and hitting BrickLink twice as fast from one address, which is exactly
    what the lock exists to prevent."""
    with scan_lock() as acquired:
        if not acquired:
            log("Another scan already holds the lock — nothing done.")
            return {"done": 0, "errors": [], "blocked": True}
        return _run_refresh_locked(scope, item_id, force, theme, limit,
                                   progress, log, inventory_only, with_figs)


def _run_refresh_locked(scope, item_id, force, theme, limit, progress, log,
                        inventory_only, with_figs):
    cfg = get_config()
    if with_figs is None:
        with_figs = scope == "priority" and not inventory_only
    conn = dbq.connect()
    try:
        targets = select_targets(conn, scope, item_id, theme)

        if limit and len(targets) > limit:
            # Targets are ordered never-scanned first, so a limited run always
            # takes the most useful slice. Say what was left out.
            log(f"… {len(targets)} items match; scanning the first {limit}, "
                f"{len(targets) - limit} left for the next run")
            targets = targets[:limit]

        errors = []
        queued = {t[0] for t in targets}
        i = 0
        # `targets` grows as sets reveal their figures, so this walks an index
        # rather than iterating a fixed list.
        while i < len(targets):
            iid, itype = targets[i][0], targets[i][1]
            total = len(targets)
            log(f"[{i + 1}/{total}] {iid}")
            if progress:
                progress(i, total, iid, errors)
            results = refresh_item(conn, iid, itype, force=force, log=log,
                                   inventory_only=inventory_only)
            for source, status in results.items():
                if status not in ("ok", "fresh", "empty"):
                    errors.append((iid, source, status))

            if with_figs and (itype or item_type_for(iid)) == "S":
                fresh_figs = [f for f in unpriced_figs_of(conn, iid)
                              if f not in queued]
                # The limit is a budget for the whole night, figures included:
                # a fig-heavy set must not silently triple the run.
                if limit:
                    fresh_figs = fresh_figs[:max(0, limit - len(targets))]
                if fresh_figs:
                    targets.extend((f, "M") for f in fresh_figs)
                    queued.update(fresh_figs)
                    log(f"  ⚙ +{len(fresh_figs)} unpriced fig(s) queued")

            i += 1
            if i < len(targets) and not cfg.fixture_mode:
                polite_sleep()
        if progress:
            progress(len(targets), len(targets), None, errors)
        return {"done": len(targets), "errors": errors}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Refresh prices across sources")
    ap.add_argument("--item", help="single item id, e.g. 75192 or sw0636")
    ap.add_argument("--scope", default="portfolio",
                    choices=["portfolio", "priority", "stale", "theme", "gaps",
                             "inventories", "all"],
                    help="priority = owned, wishlist, figs of owned sets, held "
                         "themes, then everything else (for unattended runs); "
                         "inventories = scanned sets whose minifig list was "
                         "never stored (pair with --inventory-only)")
    ap.add_argument("--theme", help="theme name for --scope theme, "
                                    "e.g. \"Super Heroes Marvel\"")
    ap.add_argument("--force", action="store_true",
                    help="ignore the freshness TTL")
    ap.add_argument("--limit", type=int,
                    help="scan at most N items (never-scanned ones first)")
    ap.add_argument("--inventory-only", action="store_true",
                    help="only fetch parts + minifig inventory, no price scrape")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be scanned, in order, and stop")
    figs = ap.add_mutually_exclusive_group()
    figs.add_argument("--with-figs", dest="with_figs", action="store_true",
                      default=None,
                      help="price the figures a set turns out to contain, in "
                           "the same run (default for --scope priority)")
    figs.add_argument("--no-figs", dest="with_figs", action="store_false",
                      help="scan only the listed targets")
    args = ap.parse_args()

    if args.theme and args.scope == "portfolio":
        args.scope = "theme"          # --theme alone implies --scope theme

    if args.dry_run:
        for i, (iid, itype, tier) in enumerate(
                plan_targets(scope=args.scope, item_id=args.item,
                             theme=args.theme, limit=args.limit), 1):
            print(f"{i:5}  {iid:14} {itype or '?':2}  {tier}")
        return

    # One scraper at a time; run_refresh holds the lock for every caller now,
    # so taking it again here would only deadlock against ourselves.
    summary = run_refresh(scope=args.scope, item_id=args.item,
                          force=args.force, theme=args.theme, limit=args.limit,
                          inventory_only=args.inventory_only,
                          with_figs=args.with_figs)
    if summary.get("blocked"):
        print(f"Another scan is already running (see {LOCK_PATH}). Nothing done.")
        raise SystemExit(3)
    print(f"\nRefreshed {summary['done']} item(s); {len(summary['errors'])} source error(s).")
    for iid, source, err in summary["errors"][:20]:
        print(f"  {iid} · {source}: {err}")


if __name__ == "__main__":
    main()
