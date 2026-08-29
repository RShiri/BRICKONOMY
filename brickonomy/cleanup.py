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
    """Rows whose stored name is really an item id — the longest-anchor bug."""
    out = []
    for r in conn.execute(
            """SELECT set_id, fig_id, fig_name FROM set_minifigs
               WHERE fig_name IS NOT NULL AND fig_name != ''"""):
        name = r["fig_name"].strip()
        # A real fig name has spaces and prose; an id has neither.
        if " " not in name and name != r["fig_id"]:
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    finally:
        conn.close()
    if not args.dry_run:
        print("Re-scan an affected set to refill the names: "
              "python -m brickonomy.refresh --item 76051 --force")


if __name__ == "__main__":
    main()
