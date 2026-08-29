"""Import the complete LEGO catalog from Rebrickable's public CSV dumps.

  python -m brickonomy.rebrickable            # every set ever released
  python -m brickonomy.rebrickable --minifigs # + the minifig catalog
  python -m brickonomy.rebrickable --from-dir downloads/   # use local files

Rebrickable publishes daily gzipped CSVs of the whole catalog, free and
without a key. That gives the site ~23,000 sets — everything BrickLink and
BrickEconomy list — so search covers the entire catalog instead of only the
items that have been price-scraped.

Only catalog metadata is imported (number, name, theme, year, part count).
Prices still come from `brickonomy.refresh`; a set nobody has scanned simply
has no price yet.
"""
import argparse
import csv
import gzip
import io
import re
from pathlib import Path

import requests

CDN = "https://cdn.rebrickable.com/media/downloads"
FILES = {"themes": "themes.csv.gz", "sets": "sets.csv.gz", "minifigs": "minifigs.csv.gz"}
USER_AGENT = "brickonomy/0.1 (+https://github.com/RShiri/BrickLink-Price-Scraper-Analyzer)"

# Rebrickable keeps non-LEGO and non-set oddities in the same table; these
# never appear on BrickLink as buyable sets.
SKIP_THEME_NAMES = {"Books", "Gear", "Key Chain", "Magnets", "Promotional"}


def fetch_csv(name, from_dir=None):
    """Yield dict rows from a Rebrickable CSV (local file or CDN download)."""
    filename = FILES[name]
    if from_dir:
        path = Path(from_dir) / filename
        raw = path.read_bytes() if path.exists() else Path(from_dir).joinpath(
            filename.removesuffix(".gz")).read_bytes()
    else:
        resp = requests.get(f"{CDN}/{filename}", headers={"User-Agent": USER_AGENT},
                            timeout=120)
        resp.raise_for_status()
        raw = resp.content

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))


def theme_lookup(rows):
    """{theme_id: (top_level_name, full_path)} resolved through parent ids."""
    by_id = {r["id"]: {"name": r["name"], "parent": r.get("parent_id") or None}
             for r in rows}
    out = {}
    for tid in by_id:
        chain, cur, guard = [], tid, 0
        while cur in by_id and guard < 10:
            chain.append(by_id[cur]["name"])
            cur = by_id[cur]["parent"]
            guard += 1
        chain.reverse()
        out[tid] = (chain[0] if chain else None, " / ".join(chain))
    return out


def import_catalog(conn, from_dir=None, with_minifigs=False, log=print):
    """Upsert every set (and optionally minifig) into items. Returns counts."""
    from . import db as dbq

    themes = theme_lookup(fetch_csv("themes", from_dir))
    log(f"✔ themes: {len(themes)}")

    sets_rows = fetch_csv("sets", from_dir)
    log(f"  sets in dump: {len(sets_rows)}")

    n_sets = 0
    for row in sets_rows:
        set_num = (row.get("set_num") or "").strip()
        if not set_num:
            continue
        # Rebrickable numbers a set "75192-1"; BrickLink's bare number is ours.
        item_id = set_num.split("-")[0]
        if not re.match(r"^[0-9]+[a-zA-Z]?$", item_id):
            continue  # skip books/gear-style ids that aren't buyable sets

        top, path = themes.get(row.get("theme_id"), (None, None))
        if top in SKIP_THEME_NAMES:
            continue
        subtheme = path[len(top) + 3:] if path and top and len(path) > len(top) else None

        year = row.get("year")
        parts = row.get("num_parts")
        dbq.upsert_item(
            conn, item_id, item_type="S",
            name=(row.get("name") or "").strip() or None,
            theme=top, subtheme=subtheme,
            year=int(year) if (year or "").isdigit() else None,
            parts=int(parts) if (parts or "").isdigit() and int(parts) > 0 else None,
        )
        n_sets += 1
        if n_sets % 2000 == 0:
            conn.commit()
            log(f"  … {n_sets} sets")
    conn.commit()
    log(f"✔ sets imported/updated: {n_sets}")

    n_figs = 0
    if with_minifigs:
        for row in fetch_csv("minifigs", from_dir):
            fig_num = (row.get("fig_num") or "").strip()
            if not fig_num:
                continue
            dbq.upsert_item(conn, fig_num, item_type="M",
                            name=(row.get("name") or "").strip() or None)
            n_figs += 1
            if n_figs % 2000 == 0:
                conn.commit()
        conn.commit()
        log(f"✔ minifigs imported/updated: {n_figs}")

    return {"themes": len(themes), "sets": n_sets, "minifigs": n_figs}


def main():
    from . import db as dbq

    ap = argparse.ArgumentParser(description="Import the full LEGO catalog from Rebrickable")
    ap.add_argument("--minifigs", action="store_true",
                    help="also import the minifig catalog (~15k rows)")
    ap.add_argument("--from-dir", help="read the CSVs from a local directory instead")
    args = ap.parse_args()

    conn = dbq.connect()
    try:
        counts = import_catalog(conn, from_dir=args.from_dir,
                                with_minifigs=args.minifigs)
        dbq.normalize_themes(conn)
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        print(f"\nCatalog now holds {total:,} items "
              f"({counts['sets']:,} sets from this run).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
