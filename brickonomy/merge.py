"""Merge a database scanned elsewhere into this one.

  python -m brickonomy.merge downloaded/brickonomy.db --dry-run
  python -m brickonomy.merge downloaded/brickonomy.db

The theme-scan workflow scans on GitHub's runners — they have real network
access and Chrome, so a whole theme can be priced without leaving a laptop
running for hours — and commits the result to data/brickonomy.db, which is
also published as a run artifact. This brings those prices home.

Only scraped facts are merged, never anything about the collection:

  price_snapshots   appended, deduped on (item_id, source, condition, kind,
                    scraped_at). Snapshots are append-only by design, so a
                    merge is a union and can never contradict what is here.
  items             catalog metadata filled in where this database has a hole;
                    a name, year or part count already known is left alone.
  set_parts,
  set_minifigs      inventories for sets that have none here.
  part_out          taken when newer than the local row.

Deliberately NOT merged: portfolio (what is owned and what was paid — the
runner's copy is seeded from the repo CSV and is not authoritative) and
exchange_rates (local ones are fresher and the conversion has to stay
consistent with the stored history).
"""
import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db as dbq
from .config import get_config


def _count(conn, table, where=""):
    return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]


def merge(conn, incoming_path, dry_run=False, log=print):
    """Merge `incoming_path` into the open connection. Returns a summary."""
    incoming = Path(incoming_path)
    if not incoming.exists():
        raise FileNotFoundError(incoming)

    # A read-only ATTACH keeps the downloaded file honest: nothing this
    # process does can write back into it.
    uri = f"file:{incoming.resolve().as_posix()}?mode=ro"
    conn.execute("ATTACH DATABASE ? AS incoming", (uri,))
    try:
        before = {t: _count(conn, t) for t in
                  ("price_snapshots", "items", "set_parts", "set_minifigs", "part_out")}
        theirs = {t: _count(conn, f"incoming.{t}") for t in before}
        log(f"  incoming: {theirs['price_snapshots']:,} snapshots, "
            f"{theirs['items']:,} items, {theirs['set_minifigs']:,} fig rows")

        # How many snapshot rows are genuinely new. The natural key is the
        # scrape itself; two runs of the same scan produce the same tuple.
        new_snaps = conn.execute(
            """SELECT COUNT(*) FROM incoming.price_snapshots i
               WHERE NOT EXISTS (
                 SELECT 1 FROM main.price_snapshots m
                 WHERE m.item_id = i.item_id AND m.source = i.source
                   AND m.condition = i.condition AND m.kind = i.kind
                   AND m.scraped_at = i.scraped_at)""").fetchone()[0]
        new_items = conn.execute(
            """SELECT COUNT(*) FROM incoming.items i
               WHERE i.item_id NOT IN (SELECT item_id FROM main.items)"""
        ).fetchone()[0]
        log(f"  new snapshots: {new_snaps:,} · new catalog items: {new_items:,} · "
            f"duplicates skipped: {theirs['price_snapshots'] - new_snaps:,}")

        if dry_run:
            log("  dry run — nothing written")
            return {"snapshots": 0, "items": 0, "inventories": 0, "part_out": 0,
                    "would_add_snapshots": new_snaps, "would_add_items": new_items}

        conn.execute(
            """INSERT INTO main.price_snapshots
                 (item_id, source, condition, kind, currency, price_min, price_avg,
                  price_median, price_max, market_price, listing_count, total_qty,
                  confidence, scraped_at, raw_json)
               SELECT i.item_id, i.source, i.condition, i.kind, i.currency,
                      i.price_min, i.price_avg, i.price_median, i.price_max,
                      i.market_price, i.listing_count, i.total_qty,
                      i.confidence, i.scraped_at, i.raw_json
               FROM incoming.price_snapshots i
               WHERE NOT EXISTS (
                 SELECT 1 FROM main.price_snapshots m
                 WHERE m.item_id = i.item_id AND m.source = i.source
                   AND m.condition = i.condition AND m.kind = i.kind
                   AND m.scraped_at = i.scraped_at)""")

        # Catalog metadata: fill holes only. A name or year already known here
        # (from the BrickEconomy export, say) outranks the runner's copy.
        conn.execute(
            """INSERT INTO main.items (item_id, item_type, name, theme, subtheme,
                                       year, parts, minifigs, weight_g,
                                       retail_price, retail_currency)
               SELECT item_id, item_type, name, theme, subtheme, year, parts,
                      minifigs, weight_g, retail_price, retail_currency
               FROM incoming.items
               WHERE item_id NOT IN (SELECT item_id FROM main.items)""")
        conn.execute(
            """UPDATE main.items AS m
               SET theme    = COALESCE(NULLIF(m.theme, ''),
                                       (SELECT i.theme FROM incoming.items i
                                        WHERE i.item_id = m.item_id)),
                   year     = COALESCE(m.year,
                                       (SELECT i.year FROM incoming.items i
                                        WHERE i.item_id = m.item_id)),
                   parts    = COALESCE(m.parts,
                                       (SELECT i.parts FROM incoming.items i
                                        WHERE i.item_id = m.item_id)),
                   minifigs = COALESCE(m.minifigs,
                                       (SELECT i.minifigs FROM incoming.items i
                                        WHERE i.item_id = m.item_id)),
                   weight_g = COALESCE(m.weight_g,
                                       (SELECT i.weight_g FROM incoming.items i
                                        WHERE i.item_id = m.item_id))
               WHERE m.item_id IN (SELECT item_id FROM incoming.items)""")
        conn.execute(
            """UPDATE main.items AS m
               SET name = (SELECT i.name FROM incoming.items i
                           WHERE i.item_id = m.item_id)
               WHERE (m.name IS NULL OR m.name = '')
                 AND m.item_id IN (SELECT item_id FROM incoming.items
                                   WHERE name IS NOT NULL AND name != '')""")

        # Inventories: only for sets that have none here, so a locally-repaired
        # inventory is never overwritten by a runner's older parse.
        conn.execute(
            """INSERT INTO main.set_minifigs (set_id, fig_id, fig_name, qty, updated_at)
               SELECT set_id, fig_id, fig_name, qty, updated_at
               FROM incoming.set_minifigs
               WHERE set_id NOT IN (SELECT DISTINCT set_id FROM main.set_minifigs)""")
        conn.execute(
            """INSERT INTO main.set_parts (set_id, part_no, part_name, color_id,
                                           color_name, qty, avg_price,
                                           price_currency, updated_at)
               SELECT set_id, part_no, part_name, color_id, color_name, qty,
                      avg_price, price_currency, updated_at
               FROM incoming.set_parts
               WHERE set_id NOT IN (SELECT DISTINCT set_id FROM main.set_parts)""")
        conn.execute(
            """INSERT INTO main.part_out (set_id, pov_total, currency, scraped_at)
               SELECT i.set_id, i.pov_total, i.currency, i.scraped_at
               FROM incoming.part_out i
               WHERE TRUE
               ON CONFLICT(set_id) DO UPDATE SET
                 pov_total  = excluded.pov_total,
                 currency   = excluded.currency,
                 scraped_at = excluded.scraped_at
               WHERE excluded.scraped_at > part_out.scraped_at""")
        conn.commit()

        after = {t: _count(conn, t) for t in before}
        summary = {t: after[t] - before[t] for t in before}
        log(f"✔ merged {summary['price_snapshots']:,} snapshots, "
            f"{summary['items']:,} items, "
            f"{summary['set_minifigs']:,} fig rows, "
            f"{summary['set_parts']:,} part rows")
        return {"snapshots": summary["price_snapshots"],
                "items": summary["items"],
                "inventories": summary["set_minifigs"] + summary["set_parts"],
                "part_out": summary["part_out"],
                "would_add_snapshots": new_snaps, "would_add_items": new_items}
    finally:
        conn.execute("DETACH DATABASE incoming")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("incoming", nargs="?",
                    help="the downloaded database to merge in")
    ap.add_argument("--rank-themes", action="store_true",
                    help="list the themes most worth scanning on a runner, and stop")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be merged and write nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the timestamped copy of the local database")
    args = ap.parse_args()

    if args.rank_themes:
        conn = dbq.connect()
        try:
            print(f"{'theme':34} {'unscanned':>9} {'owned':>6} {'avg yr':>7} {'score':>8}")
            for t in rank_themes(conn):
                print(f"{t['theme'][:33]:34} {t['unscanned']:>9,} {t['owned']:>6} "
                      f"{t['avg_year'] or '-':>7} {t['score']:>8,}")
        finally:
            conn.close()
        return
    if not args.incoming:
        ap.error("give a database to merge, or use --rank-themes")

    path = get_config().db_path
    print(f"Merging {args.incoming} into {path}")
    if not args.dry_run and not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = Path(f"{path}.{stamp}.bak")
        shutil.copy2(path, dest)
        print(f"✔ backed up to {dest.name}")

    conn = dbq.connect()
    try:
        merge(conn, args.incoming, dry_run=args.dry_run)
    finally:
        conn.close()



def rank_themes(conn, limit=20):
    """Themes worth spending runner time on, most valuable first.

    Ranked by unscanned items weighted toward what is already collected: a
    theme with holdings earns its scan before one nobody here owns, and a
    theme already fully scanned drops off. Retired sets are what appreciate,
    so age counts too.
    """
    rows = conn.execute(
        """SELECT i.theme                                          AS theme,
                  COUNT(*)                                         AS total,
                  SUM(CASE WHEN s.item_id IS NULL THEN 1 ELSE 0 END) AS unscanned,
                  SUM(CASE WHEN p.owned > 0 THEN 1 ELSE 0 END)     AS owned,
                  AVG(CASE WHEN i.year > 0 THEN i.year END)        AS avg_year
           FROM items i
           LEFT JOIN (SELECT DISTINCT item_id FROM price_snapshots) s
             ON s.item_id = i.item_id
           LEFT JOIN portfolio p ON p.item_id = i.item_id
           WHERE i.theme IS NOT NULL AND i.theme != '' AND i.item_type = 'S'
           GROUP BY i.theme
           HAVING unscanned > 0""").fetchall()

    this_year = datetime.now().year
    out = []
    for r in rows:
        age = this_year - (r["avg_year"] or this_year)
        # Within a tier: how much is left to do, nudged by age, since retired
        # sets are the ones that actually move in price.
        score = r["unscanned"] * (1 + min(age, 20) / 20)
        out.append({"theme": r["theme"], "total": r["total"],
                    "unscanned": r["unscanned"], "owned": r["owned"],
                    "avg_year": round(r["avg_year"]) if r["avg_year"] else None,
                    "score": round(score)})
    # Holdings are a tier, not a weight. A theme you collect earns its runner
    # time ahead of one you do not, however much larger the other is —
    # a multiplier lets a big enough unowned theme outrank a collected one,
    # which is precisely the ordering this is meant to prevent.
    out.sort(key=lambda t: (t["owned"] == 0, -t["score"]))
    return out[:limit]

if __name__ == "__main__":
    main()
