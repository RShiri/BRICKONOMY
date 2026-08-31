"""Repair set inventories polluted by the old minifig-inventory parser.

  python -m brickonomy.cleanup --dry-run    # report only, touch nothing
  python -m brickonomy.cleanup              # back up, then repair

Two defects in `BrickLinkSource.parse_minifig_inventory` (both fixed) wrote bad
rows that a re-scrape alone will not fully undo:

  1. Printed parts served from BrickLink's `?M=` links — Ant-Man's statuette
     `90398pb007` and friends — were stored in `set_minifigs` as minifigures.
     Scanning one times out on a page that does not exist, and it inflates
     every "figures vs parts" total. `_refresh_minifigs` also stamped
     `item_type='M'` on those catalog rows.
  2. The fig name was taken as the longest link text in the row, so a row that
     also linked another item stored *that item's id* as the name. Set 76051's
     `sh0255` ended up called `90398pb007`, in `set_minifigs` and in `items`.

Removing the bogus inventory rows is safe: they are derived data, rebuilt by
`python -m brickonomy.refresh --item <set> --force`. Names are only cleared,
never invented, so the next scan fills them in from the fixed parser.
"""
import argparse

from . import db as dbq
from .config import get_config
from .importer import item_type_for
from .rebase import backup


def find_bad_inventory_rows(conn):
    """set_minifigs rows whose fig_id is not a minifig id."""
    return [dict(r) for r in conn.execute(
        """SELECT sm.set_id, sm.fig_id, sm.fig_name, i.item_type, i.name
           FROM set_minifigs sm LEFT JOIN items i ON i.item_id = sm.fig_id
           ORDER BY sm.set_id, sm.fig_id""")
        if item_type_for(r["fig_id"]) != "M"]


def find_bad_names(conn):
    """Rows whose stored name is really an item id — the longest-anchor bug.

    The test used to be "no spaces", on the reasoning that a real name is
    prose. Plenty of minifigures are named after one word: Sersi, Gambit,
    Rogue, Daredevil, Spider-Ham. Fifteen such rows were queued for erasure,
    every one a genuine name and none an id, and clearing them is not
    recoverable by an ordinary rescan — upsert_set_minifigs coalesces an empty
    incoming name onto the stored one, so only --force would bring them back.

    What actually distinguishes an id is a digit. Every BrickLink item id has
    one: sh0255, col334, 90398pb007, 75192. A character's name does not.
    """
    out = []
    for r in conn.execute(
            """SELECT set_id, fig_id, fig_name FROM set_minifigs
               WHERE fig_name IS NOT NULL AND fig_name != ''"""):
        name = r["fig_name"].strip()
        if " " in name or name == r["fig_id"]:
            continue
        if any(ch.isdigit() for ch in name):
            out.append(dict(r))
    return out


def clean(conn, dry_run=False, log=print):
    bad_rows = find_bad_inventory_rows(conn)
    bad_names = find_bad_names(conn)
    # Only the ids being removed here — never a blanket sweep of item_type='M'.
    # Rebrickable's catalog ids look like `fig-000123`, which item_type_for
    # does not recognise as minifigs, so a blanket check would retype ~17k
    # perfectly good figures as sets.
    ids = tuple({r["fig_id"] for r in bad_rows})
    mistyped = []
    if ids:
        mistyped = [dict(r) for r in conn.execute(
            f"""SELECT item_id, name, item_type FROM items
                WHERE item_type = 'M' AND item_id IN ({','.join('?' * len(ids))})""",
            ids)]

    log(f"  non-minifig rows in set_minifigs: {len(bad_rows)}")
    for r in bad_rows:
        log(f"      {r['set_id']:10} -> {r['fig_id']:14} {(r['name'] or '')[:40]}")
    log(f"  fig names that are really ids:    {len(bad_names)}")
    for r in bad_names:
        log(f"      {r['set_id']:10} -> {r['fig_id']:14} named {r['fig_name']!r}")
    log(f"  catalog items mistyped as 'M':    {len(mistyped)}")
    for r in mistyped:
        log(f"      {r['item_id']:14} {(r['name'] or '')[:44]}")

    if dry_run:
        log("  dry run — nothing written")
        return {"rows": 0, "names": 0, "types": 0}

    for r in bad_rows:
        conn.execute("DELETE FROM set_minifigs WHERE set_id=? AND fig_id=?",
                     (r["set_id"], r["fig_id"]))
    for r in bad_names:
        # Cleared, not guessed: the next scan writes the real one. items.name
        # is NOT NULL, so "unknown" is the empty string, and the page then
        # shows the bare id rather than another item's number.
        conn.execute("UPDATE set_minifigs SET fig_name='' WHERE set_id=? AND fig_id=?",
                     (r["set_id"], r["fig_id"]))
        conn.execute("UPDATE items SET name='' WHERE item_id=? AND name=?",
                     (r["fig_id"], r["fig_name"]))
    for r in mistyped:
        conn.execute("UPDATE items SET item_type='S' WHERE item_id=?", (r["item_id"],))
    conn.commit()
    log(f"✔ removed {len(bad_rows)} row(s), cleared {len(bad_names)} name(s), "
        f"retyped {len(mistyped)} item(s)")
    return {"rows": len(bad_rows), "names": len(bad_names), "types": len(mistyped)}


def derivable_minifig_themes(conn):
    """[(fig_id, theme)] for figures whose theme can be inferred.

    A figure carries no theme of its own — eight of 17,465 have one — which
    left the theme filter on the Minifigures tab offering two entries of four
    items each. The theme of the earliest set a figure appears in is its
    theme, by the same reasoning that dates it: that set is its debut. Only
    one figure in this catalog appears under two themes, a generic skeleton,
    and taking the debut resolves it the same way.
    """
    return [(r["fig_id"], r["theme"]) for r in conn.execute(
        """SELECT sm.fig_id, s.theme
           FROM set_minifigs sm
           JOIN items f ON f.item_id = sm.fig_id
           JOIN items s ON s.item_id = sm.set_id
           WHERE (f.theme IS NULL OR f.theme = '')
             AND s.theme IS NOT NULL AND s.theme != ''
           GROUP BY sm.fig_id
           HAVING s.year = MIN(s.year) OR MIN(s.year) IS NULL""")]


def backfill_minifig_themes(conn, dry_run=False, log=print):
    """Give each themeless figure the theme of the earliest set holding it."""
    rows = derivable_minifig_themes(conn)
    log(f"  figures that can take a theme from their earliest set: {len(rows)}")
    for fig_id, theme in rows[:6]:
        log(f"      {fig_id:12} -> {theme}")
    if len(rows) > 6:
        log(f"      … and {len(rows) - 6} more")
    if dry_run:
        log("  dry run — nothing written")
        return 0
    conn.executemany("UPDATE items SET theme=? WHERE item_id=? AND "
                     "(theme IS NULL OR theme='')",
                     [(dbq.canonical_theme(theme), fig_id)
                      for fig_id, theme in rows])
    conn.commit()
    log(f"✔ themed {len(rows)} figure(s)")
    return len(rows)


def derivable_minifig_years(conn):
    """[(fig_id, year, sets)] for figures whose release year can be inferred.

    Rebrickable's figure records mostly carry no year — 17,204 of them — and
    the year is not decoration: it drives the lifecycle phase, the growth
    estimate and the retirement window, all of which fall back to nothing
    without it. But a figure's debut is the release year of the earliest set
    it appears in, and the set inventories give exactly that.
    """
    return [(r["fig_id"], r["year"], r["sets"]) for r in conn.execute(
        """SELECT sm.fig_id, MIN(s.year) AS year, COUNT(*) AS sets
           FROM set_minifigs sm
           JOIN items f ON f.item_id = sm.fig_id
           JOIN items s ON s.item_id = sm.set_id
           WHERE (f.year IS NULL OR f.year = 0) AND s.year > 0
           GROUP BY sm.fig_id""")]


def backfill_minifig_years(conn, dry_run=False, log=print):
    """Give each yearless figure the release year of the earliest set holding
    it. Idempotent: a figure that already has a year is never touched, so a
    year corrected by hand or by a later catalog import stands."""
    rows = derivable_minifig_years(conn)
    log(f"  figures that can take a year from their earliest set: {len(rows)}")
    for fig_id, year, sets in rows[:10]:
        log(f"      {fig_id:12} -> {year}  (appears in {sets} set(s))")
    if len(rows) > 10:
        log(f"      … and {len(rows) - 10} more")
    if dry_run:
        log("  dry run — nothing written")
        return 0
    conn.executemany("UPDATE items SET year=? WHERE item_id=? AND "
                     "(year IS NULL OR year=0)",
                     [(year, fig_id) for fig_id, year, _ in rows])
    conn.commit()
    log(f"✔ dated {len(rows)} figure(s)")
    return len(rows)


def compactable_runs(conn):
    """Runs of three or more consecutive snapshots holding the same price.

    A scan burst writes the same figure several times in an evening — three
    identical rows minutes apart say nothing four don't. Keeping the first and
    last of each run preserves the shape of every chart exactly (the segment
    between them is a straight line either way) and preserves *when* the price
    was known to start and stop holding, which is the only information the
    middle rows carried.
    """
    rows = conn.execute(
        """SELECT id, item_id, source, condition, kind,
                  COALESCE(market_price, price_avg, -1) AS v, scraped_at
           FROM price_snapshots
           ORDER BY item_id, source, condition, kind, scraped_at, id""").fetchall()
    runs, prev_key, run = [], None, []
    for r in rows:
        key = (r["item_id"], r["source"], r["condition"], r["kind"], r["v"])
        if key == prev_key:
            run.append(r)
        else:
            if len(run) > 2:
                runs.append(run)
            prev_key, run = key, [r]
    if len(run) > 2:
        runs.append(run)
    return runs


def compact_snapshots(conn, dry_run=False, log=print):
    """Drop the interior of each identical-price run. Returns rows removed."""
    runs = compactable_runs(conn)
    doomed = [r["id"] for run in runs for r in run[1:-1]]
    total = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
    log(f"  snapshot rows: {total:,}")
    log(f"  identical-price runs of 3+: {len(runs)} · interior rows: {len(doomed):,}")
    for run in sorted(runs, key=len, reverse=True)[:5]:
        log(f"      {run[0]['item_id']:12} {run[0]['source']:10} "
            f"{run[0]['condition']:5} {run[0]['kind']:7} x{len(run)}")
    if dry_run or not doomed:
        if dry_run:
            log("  dry run — nothing written")
        return 0
    conn.executemany("DELETE FROM price_snapshots WHERE id = ?",
                     [(i,) for i in doomed])
    conn.commit()
    log(f"✔ removed {len(doomed):,} rows; {total - len(doomed):,} remain")
    return len(doomed)


def audit_indexes(conn, log=print):
    """Report whether the hot query paths are covered. Creates nothing that
    already exists; the point is to notice when a new query has no index."""
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='price_snapshots'")}
    wanted = {
        "idx_snapshots_lookup":
            "latest_snapshot / is_fresh — (item_id, source, condition, kind, scraped_at)",
    }
    for name, why in wanted.items():
        log(f"  {'✔' if name in have else '✘'} {name}: {why}")
    # The scheduler asks "which items have ever been scanned" on every run;
    # the leading item_id of the lookup index serves it.
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT DISTINCT item_id FROM price_snapshots").fetchall()
    detail = " ".join(str(p["detail"]) for p in plan)
    log(f"  scheduler's coverage probe: {detail}")
    return have


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compact", action="store_true",
                    help="also collapse runs of identical consecutive snapshots")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the timestamped database copy")
    args = ap.parse_args()

    path = get_config().db_path
    print(f"Cleaning minifig inventories in {path}")
    if not args.dry_run and not args.no_backup:
        backup(path)

    conn = dbq.connect()
    try:
        clean(conn, dry_run=args.dry_run)
        print("Dating figures from the sets they appear in")
        backfill_minifig_years(conn, dry_run=args.dry_run)
        print("Theming figures from the sets they appear in")
        backfill_minifig_themes(conn, dry_run=args.dry_run)
        if args.compact:
            print("Compacting the snapshot history")
            compact_snapshots(conn, dry_run=args.dry_run)
            print("Index audit")
            audit_indexes(conn)
    finally:
        conn.close()
    if not args.dry_run:
        print("Re-scan an affected set to refill the names: "
              "python -m brickonomy.refresh --item 76051 --force")


if __name__ == "__main__":
    main()
