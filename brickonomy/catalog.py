"""BrickLink catalog import — themes/subthemes tree and full set listings.

  python -m brickonomy.catalog --tree                 # categories only (1 request)
  python -m brickonomy.catalog --category 65          # one category's sets
  python -m brickonomy.catalog --category 65 --deep   # + all its subcategories
  python -m brickonomy.catalog --all                  # the ENTIRE set catalog
                                                      #   (thousands of sets, hours
                                                      #    of polite-rate scraping)

The tree comes from catalogTree.asp; per-category set lists from
catalogList.asp (paginated). Imported sets get name/year/category and the
theme/subtheme strings derived from the category path — prices are NOT
fetched here (use brickonomy.refresh for that).
"""
import argparse

from . import db as dbq
from .scrapers.base import polite_sleep
from .scrapers.bricklink import BrickLinkSource


def sync_tree(conn, src: BrickLinkSource, item_type="S", log=print):
    cats, err = src.fetch_category_tree(item_type)
    if err:
        log(f"✘ category tree: {err}")
        return 0
    # The rows arrive in render order; rebuild parents from depths.
    stack = []  # [(depth, cat_id, path)]
    for c in cats:
        while stack and stack[-1][0] >= c["depth"]:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        path = f"{stack[-1][2]} / {c['name']}" if stack else c["name"]
        dbq.upsert_category(conn, c["cat_id"], c["name"], parent_id=parent_id,
                            depth=c["depth"], path=path, item_type=item_type)
        stack.append((c["depth"], c["cat_id"], path))
    conn.commit()
    log(f"✔ category tree: {len(cats)} categories")
    return len(cats)


def _theme_from_path(path: str):
    parts = [p.strip() for p in (path or "").split("/") if p.strip()]
    theme = parts[0] if parts else None
    subtheme = " / ".join(parts[1:]) or None
    return theme, subtheme


def sync_category(conn, src: BrickLinkSource, cat_id: int, item_type="S",
                  log=print, max_pages=50):
    row = conn.execute(
        "SELECT * FROM categories WHERE cat_id=? AND item_type=?",
        (cat_id, item_type),
    ).fetchone()
    path = row["path"] if row else None
    theme, subtheme = _theme_from_path(path)

    imported, page, total_pages = 0, 1, 1
    while page <= total_pages and page <= max_pages:
        sets, pages, err = src.fetch_catalog_page(cat_id, page, item_type)
        if err:
            log(f"  ✘ cat {cat_id} p{page}: {err}")
            break
        total_pages = max(total_pages, pages)
        for s in sets:
            dbq.upsert_item(conn, s["item_id"], item_type=item_type,
                            name=s["name"] or None, year=s["year"],
                            theme=theme, subtheme=subtheme, category_id=cat_id)
            imported += 1
        conn.commit()
        log(f"  ✔ cat {cat_id} ({path or '?'}) p{page}/{total_pages}: {len(sets)} sets")
        page += 1
        if page <= total_pages:
            polite_sleep()
    return imported


def sync_all(conn, src: BrickLinkSource, item_type="S", log=print):
    cats = dbq.get_categories(conn, item_type)
    if not cats:
        sync_tree(conn, src, item_type, log)
        cats = dbq.get_categories(conn, item_type)
    total = 0
    for i, c in enumerate(cats):
        log(f"[{i + 1}/{len(cats)}] {c['path']}")
        total += sync_category(conn, src, c["cat_id"], item_type, log)
        polite_sleep()
    return total


def main():
    ap = argparse.ArgumentParser(description="Import the BrickLink set catalog")
    ap.add_argument("--tree", action="store_true", help="sync the category tree only")
    ap.add_argument("--category", type=int, help="sync one category's sets")
    ap.add_argument("--deep", action="store_true",
                    help="with --category: include all subcategories")
    ap.add_argument("--all", action="store_true",
                    help="sync every category (the whole set catalog — slow!)")
    ap.add_argument("--type", default="S", choices=["S", "M"],
                    help="item type: S sets (default) or M minifigs")
    args = ap.parse_args()

    conn = dbq.connect()
    src = BrickLinkSource()
    try:
        if args.tree or not (args.category or args.all):
            sync_tree(conn, src, args.type)
        if args.category:
            sync_tree(conn, src, args.type)  # keep paths fresh; 1 request
            ids = ([args.category] if not args.deep
                   else dbq.category_subtree_ids(conn, args.category, args.type))
            total = 0
            for cid in ids:
                total += sync_category(conn, src, cid, args.type)
            print(f"Imported/updated {total} sets from {len(ids)} categor(ies).")
        elif args.all:
            total = sync_all(conn, src, args.type)
            print(f"Imported/updated {total} sets across the whole catalog.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
