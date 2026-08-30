"""brickonomy.db — plain sqlite3, append-only price snapshots.

Schema is the user-specified DDL (see plan). All SQL for the app lives here.
"""
import json
import sqlite3
from datetime import datetime, timedelta

from .config import get_config

DDL = """
CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    item_type TEXT DEFAULT 'S',
    name TEXT NOT NULL,
    theme TEXT, subtheme TEXT, year INTEGER,
    parts INTEGER, minifigs INTEGER, weight_g REAL,
    retail_price REAL, retail_currency TEXT DEFAULT 'USD',
    brickowl_boid TEXT, ebay_query TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    source TEXT NOT NULL,
    condition TEXT NOT NULL,
    kind TEXT NOT NULL,
    currency TEXT NOT NULL,
    price_min REAL, price_avg REAL, price_median REAL, price_max REAL,
    market_price REAL,
    listing_count INTEGER, total_qty INTEGER,
    confidence TEXT DEFAULT 'MEDIUM',
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY(item_id) REFERENCES items(item_id)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup
ON price_snapshots (item_id, source, condition, kind, scraped_at);

CREATE TABLE IF NOT EXISTS portfolio (
    item_id TEXT PRIMARY KEY,
    owned INTEGER DEFAULT 1, wanted INTEGER DEFAULT 0,
    purchase_price REAL, purchase_currency TEXT DEFAULT 'USD',
    purchase_date TEXT,
    condition TEXT DEFAULT 'new',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(item_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS set_parts (
    set_id TEXT NOT NULL, part_no TEXT NOT NULL,
    part_name TEXT, color_id INTEGER, color_name TEXT,
    qty INTEGER NOT NULL,
    avg_price REAL, price_currency TEXT DEFAULT 'ILS',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (set_id, part_no, color_id),
    FOREIGN KEY(set_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS part_out (
    set_id TEXT PRIMARY KEY,
    pov_total REAL NOT NULL, currency TEXT DEFAULT 'ILS',
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(set_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    base TEXT NOT NULL, quote TEXT NOT NULL, rate REAL NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (base, quote)
);

CREATE TABLE IF NOT EXISTS categories (      -- BrickLink catalog tree
    cat_id INTEGER NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'S',
    name TEXT NOT NULL,
    parent_id INTEGER,                       -- NULL for top-level themes
    depth INTEGER DEFAULT 0,
    path TEXT,                               -- "Star Wars / Ultimate Collector Series"
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cat_id, item_type)
);

CREATE TABLE IF NOT EXISTS set_minifigs (    -- which figs are in which set
    set_id TEXT NOT NULL,
    fig_id TEXT NOT NULL,
    fig_name TEXT,
    qty INTEGER DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (set_id, fig_id),
    FOREIGN KEY(set_id) REFERENCES items(item_id)
);
"""

MIGRATIONS = [
    "ALTER TABLE items ADD COLUMN category_id INTEGER",
]


def connect(db_path: str = None) -> sqlite3.Connection:
    path = db_path or get_config().db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # A scan holds a write transaction for as long as it takes to scrape an
    # item. Under the default rollback journal that blocks every page read,
    # so browsing while scanning raises "database is locked". WAL lets
    # readers through; the timeout absorbs the brief writer-vs-writer waits.
    # journal_mode is persisted in the file, so the CLI, the web app and the
    # exporter all inherit it.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
    except sqlite3.OperationalError:
        pass  # e.g. a filesystem with no WAL support — plain journal is fine
    conn.executescript(DDL)
    for migration in MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── items ────────────────────────────────────────────────────────────────

ITEM_META_COLS = ("name", "theme", "subtheme", "year", "parts", "minifigs",
                  "weight_g", "retail_price", "retail_currency",
                  "brickowl_boid", "ebay_query", "item_type", "category_id")


# Each source names themes its own way — Rebrickable says "Super Heroes
# Marvel", BrickEconomy exports say "Marvel Super Heroes", BrickLink says
# "Super Heroes". Left alone they split one theme across several rows of the
# coverage table and the Themes page. Canonical name on the right.
THEME_ALIASES = {
    "marvel super heroes": "Super Heroes Marvel",
    "super heroes marvel": "Super Heroes Marvel",
    "marvel": "Super Heroes Marvel",
    "dc super heroes": "Super Heroes DC",
    "super heroes dc": "Super Heroes DC",
    "dc comics super heroes": "Super Heroes DC",
    "star wars": "Star Wars",
    "harry potter": "Harry Potter",
    "creator expert": "Icons",
    "lego icons": "Icons",
    "icons": "Icons",
    "collectable minifigures": "Collectible Minifigures",
    "collectible minifigures": "Collectible Minifigures",
    "minifigures": "Collectible Minifigures",
}


def canonical_theme(theme):
    if not theme:
        return theme
    return THEME_ALIASES.get(theme.strip().lower(), theme.strip())


def normalize_themes(conn, log=print):
    """Fold aliased theme names into their canonical spelling. Idempotent —
    run it after any import."""
    changed = 0
    rows = conn.execute(
        "SELECT DISTINCT theme FROM items WHERE theme IS NOT NULL AND theme != ''"
    ).fetchall()
    for row in rows:
        canon = canonical_theme(row["theme"])
        if canon != row["theme"]:
            cur = conn.execute("UPDATE items SET theme = ? WHERE theme = ?",
                               (canon, row["theme"]))
            changed += cur.rowcount
            log(f"  theme {row['theme']!r} → {canon!r} ({cur.rowcount} items)")
    conn.commit()
    if changed:
        log(f"✔ {changed} items moved onto canonical theme names")
    return changed


def upsert_item(conn, item_id: str, **cols):
    """Insert the item or update the provided (non-None) columns."""
    cols = {k: v for k, v in cols.items() if k in ITEM_META_COLS and v is not None}
    if cols.get("theme"):
        cols["theme"] = canonical_theme(cols["theme"])
    row = conn.execute("SELECT item_id FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        cols.setdefault("name", f"Set {item_id}")
        keys = ["item_id"] + list(cols)
        conn.execute(
            f"INSERT INTO items ({', '.join(keys)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(keys))}, ?, ?)",
            [item_id] + list(cols.values()) + [now_iso(), now_iso()],
        )
    elif cols:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(
            f"UPDATE items SET {sets}, updated_at = ? WHERE item_id = ?",
            list(cols.values()) + [now_iso(), item_id],
        )
    conn.commit()


def get_item(conn, item_id: str):
    return conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()


def list_items(conn, search: str = "", limit: int = 500, theme: str = "",
               item_type: str = ""):
    """Catalog rows, filtered in SQL.

    theme and item_type belong in the query, not in a comprehension over the
    result: ids sort as text, so a caller that fetched the first N rows and
    filtered afterwards saw only the themes whose ids happen to sort early —
    'Super Heroes Marvel' is mostly 76xxx and fell off the end entirely.
    """
    q = "SELECT * FROM items"
    where, args = [], []
    if search:
        like = f"%{search}%"
        where.append("(item_id LIKE ? OR name LIKE ? OR theme LIKE ?)")
        args += [like, like, like]
    if theme:
        where.append("theme = ?")
        args.append(theme)
    if item_type:
        where.append("item_type = ?")
        args.append(item_type)
    if where:
        q += " WHERE " + " AND ".join(where)
    # Priced items first. Only ~2% of the catalog has been scanned, so a
    # window ordered by bare id fills up with never-scanned imports and the
    # listing looks like it has no prices at all — on /minifigs, 9 of the
    # first 400 ids were priced while all 203 priced figs sorted past the cap.
    q += """ ORDER BY item_id IN (SELECT DISTINCT item_id FROM price_snapshots)
                 DESC, item_id LIMIT ?"""
    args.append(limit)
    return conn.execute(q, args).fetchall()


# ── snapshots ────────────────────────────────────────────────────────────

def insert_snapshot(conn, item_id, source, condition, kind, currency, *,
                    price_min=None, price_avg=None, price_median=None, price_max=None,
                    market_price=None, listing_count=None, total_qty=None,
                    confidence="MEDIUM", scraped_at=None, raw=None):
    conn.execute(
        """INSERT INTO price_snapshots
           (item_id, source, condition, kind, currency,
            price_min, price_avg, price_median, price_max, market_price,
            listing_count, total_qty, confidence, scraped_at, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item_id, source, condition, kind, currency,
         price_min, price_avg, price_median, price_max, market_price,
         listing_count, total_qty, confidence,
         scraped_at or now_iso(),
         json.dumps(raw) if raw is not None else None),
    )


def latest_snapshot(conn, item_id, source, condition, kind="market"):
    return conn.execute(
        """SELECT * FROM price_snapshots
           WHERE item_id=? AND source=? AND condition=? AND kind=?
           ORDER BY scraped_at DESC LIMIT 1""",
        (item_id, source, condition, kind),
    ).fetchone()


def snapshot_history(conn, item_id, condition="new", kind="market", sources=None):
    q = """SELECT source, currency, market_price, price_avg, confidence, scraped_at
           FROM price_snapshots
           WHERE item_id=? AND condition=? AND kind=?"""
    args = [item_id, condition, kind]
    if sources:
        q += f" AND source IN ({', '.join('?' * len(sources))})"
        args.extend(sources)
    q += " ORDER BY scraped_at"
    return conn.execute(q, args).fetchall()


def last_scrape_time(conn, item_id, source):
    row = conn.execute(
        "SELECT MAX(scraped_at) AS ts FROM price_snapshots WHERE item_id=? AND source=?",
        (item_id, source),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def last_scrape_any(conn, item_id):
    """Newest snapshot for an item across all sources, or None."""
    row = conn.execute(
        "SELECT MAX(scraped_at) AS ts FROM price_snapshots WHERE item_id=?",
        (item_id,),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def theme_coverage(conn, ttl_days: float = 30.0, limit: int = 400):
    """Per-theme scan progress: how many items exist, how many have ever been
    scanned, and how many of those are still fresh. Drives the coverage table
    on the Refresh page."""
    cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT i.theme                                   AS theme,
                  COUNT(*)                                  AS total,
                  SUM(CASE WHEN s.ts IS NOT NULL THEN 1 ELSE 0 END) AS scanned,
                  SUM(CASE WHEN s.ts > ?        THEN 1 ELSE 0 END)  AS fresh,
                  MAX(s.ts)                                 AS last_scan
           FROM items i
           LEFT JOIN (SELECT item_id, MAX(scraped_at) ts
                      FROM price_snapshots GROUP BY item_id) s
             ON s.item_id = i.item_id
           WHERE i.theme IS NOT NULL AND i.theme != ''
           GROUP BY i.theme
           ORDER BY scanned DESC, total DESC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()
    out = []
    for r in rows:
        total, scanned, fresh = r["total"], r["scanned"] or 0, r["fresh"] or 0
        out.append({
            "theme": r["theme"], "total": total, "scanned": scanned,
            "fresh": fresh, "stale": scanned - fresh, "left": total - scanned,
            "pct": round(scanned / total * 100) if total else 0,
            "last_scan": r["last_scan"],
        })
    return out


def is_fresh(conn, item_id, source, ttl_days: float) -> bool:
    ts = last_scrape_time(conn, item_id, source)
    if not ts:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(ts) < timedelta(days=ttl_days)
    except ValueError:
        return False


def market_delta(conn, item_id, condition="new", days=30, source="blended"):
    """Percent change of market price vs the closest snapshot >= `days` old."""
    latest = latest_snapshot(conn, item_id, source, condition)
    if not latest or not latest["market_price"]:
        return None
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    past = conn.execute(
        """SELECT market_price FROM price_snapshots
           WHERE item_id=? AND source=? AND condition=? AND kind='market'
             AND scraped_at <= ? AND market_price > 0
           ORDER BY scraped_at DESC LIMIT 1""",
        (item_id, source, condition, cutoff),
    ).fetchone()
    if not past or not past["market_price"]:
        return None
    return (latest["market_price"] - past["market_price"]) / past["market_price"] * 100.0


# ── portfolio ────────────────────────────────────────────────────────────

def upsert_portfolio(conn, item_id, *, owned=1, wanted=0, purchase_price=None,
                     purchase_currency="USD", purchase_date=None, condition="new",
                     merge_qty=False):
    row = conn.execute("SELECT owned FROM portfolio WHERE item_id=?", (item_id,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO portfolio (item_id, owned, wanted, purchase_price,
               purchase_currency, purchase_date, condition, added_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (item_id, owned, wanted, purchase_price, purchase_currency,
             purchase_date, condition, now_iso()),
        )
    else:
        new_owned = row["owned"] + owned if merge_qty else owned
        conn.execute(
            """UPDATE portfolio SET owned=?, wanted=?, purchase_price=?,
               purchase_currency=?, purchase_date=?, condition=? WHERE item_id=?""",
            (new_owned, wanted, purchase_price, purchase_currency,
             purchase_date, condition, item_id),
        )
    conn.commit()


def get_portfolio(conn):
    return conn.execute(
        """SELECT p.*, i.name, i.theme, i.year, i.retail_price, i.retail_currency, i.item_type
           FROM portfolio p JOIN items i ON i.item_id = p.item_id
           WHERE p.owned > 0 ORDER BY i.item_id""",
    ).fetchall()


def delete_portfolio(conn, item_id):
    conn.execute("DELETE FROM portfolio WHERE item_id=?", (item_id,))
    conn.commit()


# ── parts / part-out ─────────────────────────────────────────────────────

def upsert_set_parts(conn, set_id, parts):
    """parts: iterable of dicts {part_no, part_name, color_id, color_name, qty, avg_price?, price_currency?}"""
    for p in parts:
        conn.execute(
            """INSERT INTO set_parts
               (set_id, part_no, part_name, color_id, color_name, qty, avg_price, price_currency, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(set_id, part_no, color_id) DO UPDATE SET
                 part_name=excluded.part_name, color_name=excluded.color_name,
                 qty=excluded.qty,
                 avg_price=COALESCE(excluded.avg_price, set_parts.avg_price),
                 price_currency=excluded.price_currency,
                 updated_at=excluded.updated_at""",
            (set_id, p["part_no"], p.get("part_name"), p.get("color_id", 0),
             p.get("color_name"), p["qty"], p.get("avg_price"),
             p.get("price_currency", "ILS"), now_iso()),
        )
    conn.commit()


def get_set_parts(conn, set_id, search="", limit=200):
    q = "SELECT * FROM set_parts WHERE set_id=?"
    args = [set_id]
    if search:
        q += " AND (part_no LIKE ? OR part_name LIKE ? OR color_name LIKE ?)"
        like = f"%{search}%"
        args += [like, like, like]
    q += " ORDER BY qty DESC LIMIT ?"
    args.append(limit)
    return conn.execute(q, args).fetchall()


def parts_summary(conn, set_id):
    return conn.execute(
        """SELECT COUNT(*) AS lots, COALESCE(SUM(qty),0) AS pieces,
                  COALESCE(SUM(qty * avg_price),0) AS priced_total
           FROM set_parts WHERE set_id=?""",
        (set_id,),
    ).fetchone()


def upsert_part_out(conn, set_id, pov_total, currency="ILS"):
    conn.execute(
        """INSERT INTO part_out (set_id, pov_total, currency, scraped_at)
           VALUES (?,?,?,?)
           ON CONFLICT(set_id) DO UPDATE SET
             pov_total=excluded.pov_total, currency=excluded.currency,
             scraped_at=excluded.scraped_at""",
        (set_id, pov_total, currency, now_iso()),
    )
    conn.commit()


def get_part_out(conn, set_id):
    return conn.execute("SELECT * FROM part_out WHERE set_id=?", (set_id,)).fetchone()


# ── exchange rates ───────────────────────────────────────────────────────

def upsert_rate(conn, base, quote, rate):
    conn.execute(
        """INSERT INTO exchange_rates (base, quote, rate, fetched_at)
           VALUES (?,?,?,?)
           ON CONFLICT(base, quote) DO UPDATE SET
             rate=excluded.rate, fetched_at=excluded.fetched_at""",
        (base, quote, rate, now_iso()),
    )
    conn.commit()


def get_rates(conn, base):
    rows = conn.execute(
        "SELECT quote, rate, fetched_at FROM exchange_rates WHERE base=?", (base,)
    ).fetchall()
    return {r["quote"]: (r["rate"], r["fetched_at"]) for r in rows}


# ── categories (BrickLink catalog tree) ──────────────────────────────────

def upsert_category(conn, cat_id, name, parent_id=None, depth=0, path=None,
                    item_type="S"):
    conn.execute(
        """INSERT INTO categories (cat_id, item_type, name, parent_id, depth, path, updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(cat_id, item_type) DO UPDATE SET
             name=excluded.name, parent_id=excluded.parent_id,
             depth=excluded.depth, path=excluded.path, updated_at=excluded.updated_at""",
        (cat_id, item_type, name, parent_id, depth, path or name, now_iso()),
    )


def get_categories(conn, item_type="S", parent_id=None, top_only=False):
    q = "SELECT * FROM categories WHERE item_type=?"
    args = [item_type]
    if top_only:
        q += " AND parent_id IS NULL"
    elif parent_id is not None:
        q += " AND parent_id=?"
        args.append(parent_id)
    q += " ORDER BY name"
    return conn.execute(q, args).fetchall()


def category_set_counts(conn):
    """{cat_id: number of items} for badge counts in the theme browser."""
    rows = conn.execute(
        "SELECT category_id, COUNT(*) n FROM items WHERE category_id IS NOT NULL "
        "GROUP BY category_id"
    ).fetchall()
    return {r["category_id"]: r["n"] for r in rows}


def category_subtree_ids(conn, cat_id, item_type="S"):
    """cat_id plus all descendant category ids."""
    ids, frontier = {cat_id}, [cat_id]
    while frontier:
        rows = conn.execute(
            f"SELECT cat_id FROM categories WHERE item_type=? AND parent_id IN "
            f"({', '.join('?' * len(frontier))})",
            [item_type] + frontier,
        ).fetchall()
        frontier = [r["cat_id"] for r in rows if r["cat_id"] not in ids]
        ids.update(frontier)
    return list(ids)


# ── set ↔ minifig links ──────────────────────────────────────────────────

def upsert_set_minifigs(conn, set_id, figs):
    """figs: iterable of {id, name, qty}."""
    for f in figs:
        conn.execute(
            """INSERT INTO set_minifigs (set_id, fig_id, fig_name, qty, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(set_id, fig_id) DO UPDATE SET
                 fig_name=COALESCE(NULLIF(excluded.fig_name, ''), set_minifigs.fig_name),
                 qty=excluded.qty, updated_at=excluded.updated_at""",
            (set_id, f["id"], f.get("name") or "", max(1, int(f.get("qty") or 1)),
             now_iso()),
        )
    conn.commit()


def get_set_minifigs(conn, set_id):
    return conn.execute(
        "SELECT * FROM set_minifigs WHERE set_id=? ORDER BY fig_id", (set_id,)
    ).fetchall()


def sets_containing_fig(conn, fig_id):
    return conn.execute(
        """SELECT sm.set_id, sm.qty, i.name, i.theme, i.year, i.item_type
           FROM set_minifigs sm LEFT JOIN items i ON i.item_id = sm.set_id
           WHERE sm.fig_id=? ORDER BY i.year DESC, sm.set_id""",
        (fig_id,),
    ).fetchall()
