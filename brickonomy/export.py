"""Static site export for GitHub Pages.

  python -m brickonomy.export              # writes ./docs
  python -m brickonomy.export --out site --ccy USD

All links are relative, so the exported site works wherever it is mounted:
GitHub Pages serving /docs, the repo root, or a custom domain.

Drives the regular FastAPI app with a test client (so the static site is
pixel-identical to the live one) and saves every page/API response:

  /                     -> index.html
  /sets                 -> sets/index.html
  /sets?theme=T         -> sets/theme-<slug>.html
  /sets/<id>            -> sets/<id>.html        (sets AND minifig pages)
  /themes, /deals       -> themes.html, deals.html
  /portfolio            -> portfolio.html        (encrypted, or omitted)
  /api/...              -> api/....json          (chart data + search index)

Server-only UI (refresh, import, editing, currency switch) is hidden by the
templates when BRICKONOMY_STATIC_EXPORT is set.

The portfolio names what you own and what you paid for it, which a public
Pages site would hand to anyone. It is therefore never published in the clear:

  BRICKONOMY_PORTFOLIO_PASSWORD=... python -m brickonomy.export
        publishes it AES-256-GCM encrypted behind that password

  portfolio_public = true  (config, or BRICKONOMY_PORTFOLIO_PUBLIC=1)
        publishes it in the clear — an explicit choice, never a default

  python -m brickonomy.export
        omits the portfolio page and its history entirely

Publishing in the clear takes a deliberate setting rather than the absence of
one, so it can never happen by forgetting a password. See lockbox.py.
"""
import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace


def save_portfolio(client, out, save, log):
    """Publish the portfolio encrypted, or not at all. Returns pages written.

    Never in the clear: a public Pages site would otherwise hand every visitor
    the list of what is owned and what was paid for it.
    """
    from . import lockbox
    from .web import app as webapp

    if lockbox.publish_in_the_clear():
        # Deliberately unencrypted, by an explicit setting. The page is the
        # ordinary portfolio; there is nothing to hide it behind.
        webapp.STATIC_DEPTH = 0
        if save("/portfolio", "portfolio.html"):
            log("  · portfolio published IN THE CLEAR "
                "(portfolio_public is set)")
            save("/api/portfolio/history", "api/portfolio/history.json")
            return 1
        return 0

    password = lockbox.get_password()
    if not password:
        # A stub, not nothing: every page's nav links to portfolio.html, and a
        # missing file would be a dead link on the whole site.
        stub = SimpleNamespace(url=SimpleNamespace(path="/portfolio"))
        rendered = webapp.templates.get_template("portfolio_locked.html").render(
            request=stub, lockbox=None, static_mode=True, base_path="",
            u=webapp.static_url, ccy="ILS", ccy_symbol="₪",
            currencies=[], rates={}, job={}, img_url=webapp.img_url,
        )
        (out / "portfolio.html").write_text(rendered, encoding="utf-8")
        log("  · portfolio NOT published (set "
            f"{lockbox.ENV_VAR} to publish it password-protected)")
        return 1

    webapp.STATIC_DEPTH = 0
    page = client.get("/portfolio")
    history = client.get("/api/portfolio/history")
    if page.status_code != 200:
        log(f"  ! skip /portfolio ({page.status_code})")
        return 0

    # Only the <main> block is secret; the surrounding chrome is the same shell
    # every other page uses, and the unlock template re-renders it.
    html = page.text
    start, end = html.find("<main"), html.rfind("</main>")
    inner = html[html.find(">", start) + 1:end] if start != -1 and end != -1 else html

    blob = lockbox.encrypt(
        {"html": inner,
         "history": history.json() if history.status_code == 200 else None},
        password,
    )
    # base.html reads request.url.path to mark the active nav tab; the page is
    # rendered outside the request cycle, so a stub stands in for it.
    stub = SimpleNamespace(url=SimpleNamespace(path="/portfolio"))
    rendered = webapp.templates.get_template("portfolio_locked.html").render(
        request=stub, lockbox=blob, static_mode=True, base_path="",
        u=webapp.static_url, ccy="ILS", ccy_symbol="₪",
        currencies=[], rates={}, job={}, img_url=webapp.img_url,
    )
    (out / "portfolio.html").write_text(rendered, encoding="utf-8")
    log(f"  · portfolio published encrypted "
        f"({len(blob['ciphertext']):,} bytes of ciphertext, "
        f"PBKDF2 x{blob['iterations']:,})")
    return 1


def export(out_dir: str, ccy: str = "ILS", quiet: bool = False):
    from starlette.testclient import TestClient

    from . import db as dbq
    from .config import get_config
    from .web import app as webapp

    # static_url() and ctx() read these module globals at call time, so
    # flipping them here switches the whole app into static-export mode.
    prev = (webapp.STATIC_MODE, webapp.STATIC_DEPTH, get_config().display_currency,
            webapp.STATIC_PAGED_IDS)
    webapp.STATIC_MODE = True
    get_config().display_currency = ccy
    try:
        return _crawl(webapp, out_dir, quiet)
    finally:
        webapp.STATIC_MODE, webapp.STATIC_DEPTH = prev[0], prev[1]
        get_config().display_currency = prev[2]
        webapp.STATIC_PAGED_IDS = prev[3]


def _crawl(webapp, out_dir: str, quiet: bool):
    from starlette.testclient import TestClient

    from . import db as dbq

    client = TestClient(webapp.app)
    out = Path(out_dir)
    log = (lambda *a: None) if quiet else print

    conn = dbq.connect()
    try:
        # A full catalog is ~23k sets — far too many to render as pages. Only
        # items with scraped prices (or owned ones) get their own page; the
        # rest are searchable through api/index.json and open set.html.
        snap_ids = [r["item_id"] for r in conn.execute(
            "SELECT DISTINCT item_id FROM price_snapshots")]
        owned = [r["item_id"] for r in conn.execute(
            "SELECT item_id FROM portfolio WHERE owned > 0 OR wanted > 0")]
        # Minifigs of exported sets get pages too, so set pages never link
        # to a fig page that was not written.
        figs = [r["fig_id"] for r in conn.execute(
            "SELECT DISTINCT fig_id FROM set_minifigs")]
        item_ids = sorted(set(snap_ids) | set(owned) | set(figs))
        webapp.STATIC_PAGED_IDS = set(item_ids)
        catalog_total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        # Sets only: these become /sets?theme=... pages, and a theme carried
        # solely by minifigs would export a set listing with nothing in it.
        themes = [r["theme"] for r in conn.execute(
            """SELECT theme, COUNT(*) n FROM items
               WHERE theme IS NOT NULL AND theme != '' AND item_type = 'S'
               GROUP BY theme ORDER BY n DESC LIMIT 30""")]
    finally:
        conn.close()

    def save(route: str, path: str):
        # Links are emitted relative to the file being written, so the whole
        # site can be mounted anywhere.
        webapp.STATIC_DEPTH = path.count("/")
        resp = client.get(route)
        if resp.status_code != 200:
            log(f"  ! skip {route} ({resp.status_code})")
            return False
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
        return True

    n_pages = n_json = 0
    n_pages += save("/", "index.html")
    n_pages += save("/sets", "sets/index.html")
    # The nav links to /minifigs on every page, so it has to be written or
    # each exported page carries a dead link.
    n_pages += save("/minifigs", "minifigs.html")
    n_pages += save("/themes", "themes.html")
    n_pages += save("/deals", "deals.html")
    n_pages += save("/partout", "partout.html")
    n_pages += save_portfolio(client, out, save, log)
    n_pages += save("/set", "set.html")          # client-rendered catalog page
    for theme in themes:
        n_pages += save(f"/sets?theme={theme}", f"sets/theme-{webapp.slugify(theme)}.html")
    for iid in item_ids:
        n_pages += save(f"/sets/{iid}", f"sets/{iid}.html")
    for iid in snap_ids:
        n_json += save(f"/api/sets/{iid}/history", f"api/sets/{iid}/history.json")
    # api/portfolio/history.json is NOT written: it is the portfolio's value
    # over time, and publishing it beside an encrypted page would give away
    # exactly what the encryption is protecting. When the page is protected
    # the history travels inside the ciphertext instead.
    n_json += save("/api/index", "api/index.json")

    static_src = Path(webapp.BASE_DIR) / "static"
    shutil.copytree(static_src, out / "static", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")

    log(f"Exported {n_pages} pages + {n_json} JSON files to {out}/")
    log(f"Catalog holds {catalog_total:,} items; {len(item_ids):,} have their own "
        f"page, the rest are searchable and open set.html.")
    log("Links are relative, so the site works from /docs, the repo root, "
        "or any custom domain.")
    return n_pages, n_json


def main():
    ap = argparse.ArgumentParser(description="Export the site as static HTML for GitHub Pages")
    ap.add_argument("--out", default="docs", help="output directory (default: docs)")
    ap.add_argument("--ccy", default="ILS", choices=["ILS", "USD", "EUR", "GBP"],
                    help="display currency baked into the export")
    args = ap.parse_args()
    export(args.out, args.ccy)


if __name__ == "__main__":
    main()
