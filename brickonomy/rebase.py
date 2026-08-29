"""Rebuild the stored headline series onto the BrickLink-average basis.

  python -m brickonomy.rebase --dry-run    # report only, touch nothing
  python -m brickonomy.rebase              # back up, then rewrite

Every `source='blended'` row in the database was computed by the old
confidence-weighted blend of BrickLink + eBay + BrickOwl. eBay asks run 2-3x
over what a set sells for, so those rows read far above the real value. The
headline is now BrickLink's own average (see analytics.valuation), and this
rebuilds the persisted series to match.

Nothing is scraped: every BrickLink snapshot needed is already in the database.
The old rows are derived data, reproducible from the BrickLink rows that are
kept, and the database is copied to a timestamped backup first.
"""
import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db as dbq
from .analytics.valuation import BLEND_CURRENCY, HEADLINE_BASIS, HEADLINE_SOURCE
from .config import get_config
from .currency import convert


def _headline_at(conn, row):
    """Value for one BrickLink scrape row, or None if it carries no usable
    figure. Mirrors valuation.headline_value but for a row already in hand."""
    for kind, column, _label in HEADLINE_BASIS:
        if row["kind"] != kind:
            continue
        if not row[column] or row[column] <= 0:
            return None
        value = convert(conn, row[column], row["currency"], BLEND_CURRENCY)
        return round(value, 2) if value and value > 0 else None
    return None


def _confidence(kind, row):
    if kind != "sold":
        return row["confidence"] or "LOW"
    count = row["total_qty"] or row["listing_count"] or 0
    return "HIGH" if count >= 10 else "MEDIUM" if count >= 3 else "LOW"


def rebuild(conn, dry_run=False, log=print):
    """Replace every blended row with one derived from the BrickLink series.

    One headline row per BrickLink scrape timestamp, so history, growth and
    the charts read a single consistent basis instead of a series that changes
    meaning partway through.
    """
    kinds = {kind: column for kind, column, _ in HEADLINE_BASIS}
    priority = {kind: i for i, (kind, _, _) in enumerate(HEADLINE_BASIS)}

    old_count = conn.execute(
        "SELECT COUNT(*) FROM price_snapshots WHERE source='blended'").fetchone()[0]

    rows = conn.execute(
        f"""SELECT item_id, condition, kind, currency, price_avg, market_price,
                   listing_count, total_qty, confidence, scraped_at
            FROM price_snapshots
            WHERE source = ? AND kind IN ({','.join('?' * len(kinds))})
            ORDER BY item_id, condition, scraped_at""",
        (HEADLINE_SOURCE, *kinds),
    ).fetchall()

    # For each (item, condition, scrape time) keep the best available basis:
    # a sold average beats a market price beats an asking average.
    best = {}
    for row in rows:
        key = (row["item_id"], row["condition"], row["scraped_at"])
        current = best.get(key)
        if current is None or priority[row["kind"]] < priority[current["kind"]]:
            best[key] = row

    new_rows, skipped = [], 0
    for (item_id, condition, scraped_at), row in best.items():
        value = _headline_at(conn, row)
        if value is None:
            skipped += 1
            continue
        new_rows.append((item_id, condition, scraped_at, value,
                         _confidence(row["kind"], row)))

    items = len({r[0] for r in new_rows})
    log(f"  old blended rows:      {old_count:,}")
    log(f"  BrickLink rows read:   {len(rows):,}")
    log(f"  new headline rows:     {len(new_rows):,} across {items:,} items")
    if skipped:
        log(f"  skipped (no usable BrickLink figure): {skipped:,}")

    if dry_run:
        log("  dry run — nothing written")
        return {"written": 0, "removed": 0, "items": items}

    conn.execute("DELETE FROM price_snapshots WHERE source='blended'")
    conn.executemany(
        """INSERT INTO price_snapshots
             (item_id, source, condition, kind, currency, market_price,
              confidence, scraped_at)
           VALUES (?, 'blended', ?, 'market', ?, ?, ?, ?)""",
        [(item_id, condition, BLEND_CURRENCY, value, conf, scraped_at)
         for item_id, condition, scraped_at, value, conf in new_rows],
    )
    conn.commit()
    log(f"✔ replaced {old_count:,} rows with {len(new_rows):,}")
    return {"written": len(new_rows), "removed": old_count, "items": items}


def backup(path, log=print):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(f"{path}.{stamp}.bak")
    shutil.copy2(path, dest)
    log(f"✔ backed up to {dest.name}")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the timestamped database copy")
    args = ap.parse_args()

    path = get_config().db_path
    print(f"Rebasing headline values in {path}")
    if not args.dry_run and not args.no_backup:
        backup(path)

    conn = dbq.connect()
    try:
        rebuild(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
