"""Seed brickonomy.db from the data this repo already has.

  python -m brickonomy.importer

Idempotent:
  1. BrickEconomy CSV export → items metadata + portfolio rows.
  2. Legacy bricklink_data.db JSON blobs → items metadata + one seed snapshot
     set per item (source='bricklink', ILS, timestamped with the blob's
     updated_at) so history charts start with a data point.
"""
import csv
import json
import re
import sqlite3
from pathlib import Path

from . import db as dbq
from .config import get_config
from .currency import convert
from .snapshots import write_snapshots

MINIFIG_ID_RE = re.compile(r"^[a-z]{2,4}\d+", re.IGNORECASE)

# A printed part: digits, "pb", digits — BrickLink's convention for a part
# with a printed pattern, optionally with a mould-variant suffix. These are
# neither sets nor minifigures, but the id starts with a digit so everything
# that was not a minifig fell through to "set". Eight Ant-Man and Captain
# America statuettes were sitting in the catalog as sets, each scanned a
# dozen times as if it were one.
PRINTED_PART_RE = re.compile(r"^\d+pb\d+", re.IGNORECASE)


def _as_int(raw, default=0):
    """Quantity cell -> int. Exports occasionally carry '', '1 (x2)' or a
    stray label; one bad cell must not abort the whole import."""
    digits = re.sub(r"[^\d-]", "", str(raw or ""))
    try:
        return int(digits)
    except ValueError:
        return default


def normalize_item_id(raw: str) -> str:
    """'75192-1' -> '75192'; minifig ids pass through."""
    raw = raw.strip()
    if "-" in raw and raw.split("-")[0].isdigit():
        return raw.split("-")[0]
    return raw


def item_type_for(item_id: str) -> str:
    """'S' set, 'M' minifigure, 'P' printed part.

    'P' items are excluded from both catalog tabs, which filter on 'S' and
    'M', so they stop appearing as sets without needing to be deleted — the
    price history already scraped for them stays intact.
    """
    if MINIFIG_ID_RE.match(item_id) and not item_id[0].isdigit():
        return "M"
    if PRINTED_PART_RE.match(item_id):
        return "P"
    return "S"


def parse_money(raw: str):
    """'$549.99' -> (549.99, 'USD'); returns (None, None) when unparsable."""
    if not raw:
        return None, None
    raw = raw.strip()
    ccy = {"$": "USD", "€": "EUR", "£": "GBP", "₪": "ILS"}.get(raw[:1])
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits:
        return None, None
    return float(digits), (ccy or "USD")


# BrickEconomy's condition labels. Everything that is not factory-sealed is
# valued as used: an "as new" set is still an opened set on the second-hand
# market, and pricing it at sealed rates overstates a collection badly.
CONDITION_MAP = {
    "new": "new", "sealed": "new", "newsealed": "new",
    "usedasnew": "used", "usedcomplete": "used", "usedincomplete": "used",
    "used": "used",
}


def parse_condition(raw: str) -> str:
    return CONDITION_MAP.get(re.sub(r"[^a-z]", "", (raw or "").lower()), "new")


def sold_off(conn, holdings, item_types=("S",)):
    """Owned holdings absent from an export — the ones sold since it was made.

    Scoped to the item types the export actually covers: a BrickEconomy *sets*
    export lists no minifigures, so treating every missing id as sold would
    wipe out a figure collection the file never described.
    """
    placeholders = ",".join("?" * len(item_types))
    rows = conn.execute(
        f"""SELECT p.item_id, p.owned, p.purchase_price, p.purchase_currency,
                   i.name, i.item_type
            FROM portfolio p LEFT JOIN items i ON i.item_id = p.item_id
            WHERE p.owned > 0 AND COALESCE(i.item_type, 'S') IN ({placeholders})
            ORDER BY p.item_id""",
        item_types,
    ).fetchall()
    return [dict(r) for r in rows if r["item_id"] not in holdings]


def import_csv(conn, csv_path: str, prune: bool = False, log=print) -> dict:
    """Import a BrickEconomy set export. Returns a summary dict.

    prune removes owned sets that the export no longer lists — the ones sold
    since. Off by default: an import that silently deletes holdings is worse
    than one that leaves a stale row behind. The summary always reports the
    candidates under 'sold' so a caller can show them before committing.

    Two export shapes are in the wild and both are supported:
      old  Number,Theme,Subtheme,Year,Name,Owned,Wanted,Retail
      new  Number,Theme,Subtheme,Year,SetName,Retail,Paid,Value,Condition,Date,Notes

    The new one carries what was actually paid and the condition of each set,
    which the old one did not. A row in a "my sets" export is owned even when
    the file has no Owned column, so its absence means 1, not 0 — reading it
    as 0 is why a new-format export imported nothing into the portfolio.
    """
    path = Path(csv_path)
    if not path.exists():
        log(f"[importer] CSV not found: {path} — skipping portfolio seed")
        # Same shape as a real run, so callers can read every key unguarded.
        return {"rows": 0, "portfolio": 0, "wishlist": 0, "skipped": 0,
                "duplicates": 0, "sold": [], "removed": 0}

    summary = {"rows": 0, "portfolio": 0, "wishlist": 0, "skipped": 0,
               "duplicates": 0, "sold": [], "removed": 0}
    # A set owned twice is two rows in the export, at whatever each copy cost.
    # Writing them one at a time would let the second overwrite the first and
    # quietly lose a copy, so rows are accumulated per item and written once.
    holdings = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = set(reader.fieldnames or [])
        has_owned = "Owned" in cols or "Wanted" in cols

        for row in reader:
            number = (row.get("Number") or "").strip()
            if not number:
                summary["skipped"] += 1
                continue
            item_id = normalize_item_id(number)
            retail, retail_ccy = parse_money(row.get("Retail", ""))
            paid, paid_ccy = parse_money(row.get("Paid", ""))
            year = None
            if (row.get("Year") or "").strip().isdigit():
                year = int(row["Year"].strip())

            dbq.upsert_item(
                conn, item_id,
                item_type=item_type_for(item_id),
                # BrickEconomy renamed this column between exports.
                name=((row.get("SetName") or row.get("Name") or "").strip()
                      or None),
                theme=dbq.theme_as_catalogued(
                    conn, (row.get("Theme") or "").strip() or None),
                subtheme=(row.get("Subtheme") or "").strip() or None,
                year=year,
                retail_price=retail,
                retail_currency=retail_ccy,
            )

            if has_owned:
                # A stray non-numeric cell must not abort the whole import —
                # and on the web path it would surface as a 500.
                owned = _as_int(row.get("Owned"))
                wanted = _as_int(row.get("Wanted"))
            else:
                owned, wanted = 1, 0

            if owned > 0 or wanted > 0:
                held = holdings.get(item_id)
                cost = paid if paid is not None else retail
                cost_ccy = (paid_ccy if paid is not None else retail_ccy) or "USD"
                condition = parse_condition(row.get("Condition", ""))
                date = (row.get("Date") or "").strip() or None
                if held is None:
                    holdings[item_id] = {
                        "owned": owned, "wanted": wanted,
                        # Per-currency, because two copies of the same set can
                        # be bought in different ones and adding them as a bare
                        # number would silently treat 100 USD as 100 ILS.
                        "spent": {cost_ccy: (cost or 0.0) * owned} if cost else {},
                        "priced": owned if cost else 0,
                        "currency": cost_ccy,
                        "date": date, "condition": condition,
                    }
                else:
                    summary["duplicates"] += 1
                    held["owned"] += owned
                    held["wanted"] += wanted
                    if cost:
                        held["spent"][cost_ccy] = (held["spent"].get(cost_ccy, 0.0)
                                                   + cost * owned)
                        held["priced"] += owned
                    held["date"] = held["date"] or date
                    # One row holds one condition; if copies differ, keep the
                    # cautious one so the holding is not valued as sealed.
                    if condition == "used":
                        held["condition"] = "used"
            summary["rows"] += 1

    for item_id, h in holdings.items():
        # Per-copy average in one currency, so owned x price still totals what
        # was actually spent. Copies bought in other currencies are converted
        # into the first one rather than added as bare numbers.
        avg, target = None, h["currency"]
        if h["priced"]:
            total = 0.0
            for ccy, amount in h["spent"].items():
                if ccy == target:
                    total += amount
                    continue
                try:
                    total += convert(conn, amount, ccy, target)
                except ValueError:
                    # No rate for that pair: drop the copy from the average
                    # rather than pretend the number is in `target`.
                    log(f"[importer] {item_id}: no {ccy}->{target} rate, "
                        f"excluding {amount:.2f} {ccy} from the average")
                    h["priced"] -= 1
            avg = (total / h["priced"]) if h["priced"] else None
        dbq.upsert_portfolio(
            conn, item_id,
            owned=h["owned"], wanted=h["wanted"],
            purchase_price=avg,                # what it cost, not the retail
            purchase_currency=target,
            purchase_date=h["date"],
            condition=h["condition"],
        )
        summary["portfolio"] += 1 if h["owned"] > 0 else 0
        summary["wishlist"] += 1 if h["wanted"] > 0 else 0

    # An export is a snapshot of what is owned *now*: anything owned locally
    # that it does not list has been sold. Always reported, only acted on
    # when asked.
    types = tuple({item_type_for(i) for i in holdings}) or ("S",)
    summary["sold"] = sold_off(conn, set(holdings), item_types=types)
    if prune:
        for row in summary["sold"]:
            dbq.delete_portfolio(conn, row["item_id"])
        summary["removed"] = len(summary["sold"])
    return summary


def import_legacy_blobs(conn, legacy_db_path: str) -> int:
    path = Path(legacy_db_path)
    if not path.exists():
        print(f"[importer] legacy DB not found: {path} — skipping snapshot seed")
        return 0

    legacy = sqlite3.connect(path)
    legacy.row_factory = sqlite3.Row
    seeded = 0
    for row in legacy.execute("SELECT item_id, json_data, updated_at FROM items"):
        item_id = normalize_item_id(row["item_id"])
        try:
            data = json.loads(row["json_data"])
        except (TypeError, json.JSONDecodeError):
            continue
        meta = data.get("meta", {})
        specs = meta.get("specs", {})

        name = meta.get("item_name")
        dbq.upsert_item(
            conn, item_id,
            item_type=item_type_for(item_id),
            name=name if name and name != "Unknown" else None,
            year=meta.get("year_released"),
            parts=specs.get("parts") or None,
            minifigs=specs.get("minifigs") or None,
            weight_g=specs.get("weight_g") or None,
        )

        scraped_at = row["updated_at"] or meta.get("timestamp")
        # Idempotence: one seed per (item, source, timestamp)
        existing = conn.execute(
            """SELECT 1 FROM price_snapshots
               WHERE item_id=? AND source='bricklink' AND scraped_at=? LIMIT 1""",
            (item_id, scraped_at),
        ).fetchone()
        if existing:
            continue

        analysis = write_snapshots(conn, item_id, "bricklink", "ILS", data,
                                   scraped_at=scraped_at)

        # With a single source, the blended series equals it — seed that too so
        # charts and deltas have a blended point from day one.
        for condition in ("new", "used"):
            mkt = analysis[condition]["market_price"]
            if mkt and mkt > 0:
                dbq.insert_snapshot(
                    conn, item_id, "blended", condition, "market", "ILS",
                    market_price=mkt,
                    confidence=analysis[condition]["confidence"],
                    scraped_at=scraped_at,
                )
        conn.commit()
        seeded += 1
    legacy.close()
    return seeded


def import_legacy_inventories(conn, legacy_db_path: str) -> int:
    """Seed set_minifigs from the root scraper's inventory_lists table."""
    path = Path(legacy_db_path)
    if not path.exists():
        return 0
    legacy = sqlite3.connect(path)
    legacy.row_factory = sqlite3.Row
    seeded = 0
    try:
        for row in legacy.execute("SELECT set_id, json_data FROM inventory_lists"):
            set_id = normalize_item_id(row["set_id"])
            try:
                figs = json.loads(row["json_data"])
            except (TypeError, json.JSONDecodeError):
                continue
            figs = [f for f in figs if f.get("id")]
            if not figs:
                continue
            if dbq.get_set_minifigs(conn, set_id):
                continue  # a scraped inventory (with real qty) wins over the seed
            dbq.upsert_set_minifigs(conn, set_id, figs)
            seeded += 1
    finally:
        legacy.close()
    return seeded


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Seed brickonomy.db from CSV + legacy DB")
    ap.add_argument("--csv", help="BrickEconomy export to import "
                                  "(default: portfolio_csv from config)")
    ap.add_argument("--csv-only", action="store_true",
                    help="import the CSV and skip the legacy-DB seeding")
    ap.add_argument("--prune", action="store_true",
                    help="remove owned sets the export no longer lists "
                         "(sold since); without it they are only reported")
    args = ap.parse_args()

    cfg = get_config()
    conn = dbq.connect()

    summary = import_csv(conn, args.csv or cfg.portfolio_csv, prune=args.prune)
    print(f"[importer] CSV rows processed: {summary['rows']}"
          f" — portfolio {summary['portfolio']}, wishlist {summary['wishlist']}"
          f", skipped {summary['skipped']}")

    if summary["sold"]:
        verb = "removed" if args.prune else "in the portfolio but NOT in the export"
        print(f"[importer] {len(summary['sold'])} set(s) {verb}:")
        for row in summary["sold"]:
            print(f"    {row['item_id']:10} {(row['name'] or '')[:44]}")
        if not args.prune:
            print("[importer] re-run with --prune to remove them")

    if args.csv_only:
        pf = conn.execute("SELECT COUNT(*) c FROM portfolio").fetchone()["c"]
        print(f"[importer] portfolio now holds {pf} rows")
        conn.close()
        return

    n_blobs = import_legacy_blobs(conn, cfg.legacy_db_path)
    print(f"[importer] legacy items seeded with snapshots: {n_blobs}")

    n_inv = import_legacy_inventories(conn, cfg.legacy_db_path)
    print(f"[importer] legacy set inventories seeded: {n_inv}")

    dbq.normalize_themes(conn, log=lambda m: print(f"[importer]{m}"))
    items = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    pf = conn.execute("SELECT COUNT(*) c FROM portfolio").fetchone()["c"]
    snaps = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
    print(f"[importer] totals — items: {items}, portfolio: {pf}, snapshots: {snaps}")
    conn.close()


if __name__ == "__main__":
    main()
