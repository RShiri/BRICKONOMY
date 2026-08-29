# Brickonomy

A self-hosted, BrickEconomy-style LEGO value tracker that lives inside this
repo and builds on its existing BrickLink scraper and `PriceAnalyzer`.

- **Three price sources, no API keys**: BrickLink (the repo's existing Selenium
  scraper, the only one with real sale history), **eBay** and **BrickOwl**
  (both current asks) —
  all plain scraping.
- **Real price history**: every scan appends snapshots to `brickonomy.db`
  instead of overwriting, so every set gets a price-over-time chart.
- **Blended market value**: per-source PriceAnalyzer market prices are
  currency-converted and combined with confidence weights (HIGH=3, MEDIUM=2,
  LOW=1) into one blended series.
- **Growth & forecast**: growth vs retail, annualized CAGR, and a 2y/5y trend
  forecast with retirement-phase adjustment (±20% band — a trend estimate,
  not financial advice).
- **Parts inventory & part-out value**: the exact parts list from the
  BrickLink set page plus the part-out total from its POV page.
- **Portfolio & wishlist**: seeded from your BrickEconomy CSV export; import
  more via CSV / BrickLink wanted-list XML / plain set lists; edit rows inline.
  Wanted items get their own list with live "best offer" pricing.
- **Deals**: every live listing priced under blended market value after fees,
  rated (good / excellent / great invest) exactly like the analyzer's sniper.
- **Themes**: per-theme totals, average growth per year and best performer.
- **Instant search** in the header, over the whole catalog.
- **Configurable currency**: ILS / USD / EUR / GBP, ECB rates via
  frankfurter.app (no key), with cached and hardcoded fallbacks.

## Quick start

**Not a developer?** Double-click **`run.bat`** in the repo root (Windows) or
run `./run.sh` (macOS/Linux) — it creates the environment, installs
everything, seeds the database, imports the full catalog and opens the
browser. Prices are collected with `scan.bat` / `./scan.sh`. The step-by-step
guide is [`QUICKSTART.md`](../QUICKSTART.md).

By hand:

```bash
pip install -r brickonomy/requirements.txt

# 1. Seed the catalog + portfolio from what the repo already has
#    (BrickEconomy-Sets(2).csv and bricklink_data.db)
python -m brickonomy.importer

# 2. Start the web app
uvicorn brickonomy.web.app:app --reload
# open http://127.0.0.1:8000
```

Refresh prices from the header button, the Refresh & Sources page, or the CLI:

```bash
python -m brickonomy.refresh --item 75192       # one set
python -m brickonomy.refresh --scope portfolio  # everything you own
python -m brickonomy.refresh --scope stale      # only sets older than the TTL
```

Cron example (daily 06:00):

```
0 6 * * * cd /path/to/repo && python -m brickonomy.refresh --scope stale
```

## The full LEGO catalog (every set ever released)

```bash
python -m brickonomy.rebrickable            # ~23,000 sets
python -m brickonomy.rebrickable --minifigs # + ~15,000 minifigs
```

Rebrickable publishes the whole catalog as daily CSV dumps — free, no key.
After importing, the Sets page and the header search cover every set that
exists, like BrickLink or BrickEconomy. Catalog rows carry number, name,
theme, year and part count; **prices still come from `brickonomy.refresh`**,
so a set nobody has scanned shows "not scanned" and opens a lightweight page
with links out to BrickLink / BrickEconomy / eBay.

In the static build only priced items get their own page (23k pages would be
gigabytes); everything else is searchable through `api/index.json` and opens
`set.html?id=…`, which renders client-side. The **catalog-refresh** workflow
(`.github/workflows/catalog-refresh.yml`, weekly or on demand) runs the import
and rebuilds `docs/` on GitHub's runners, so the published site keeps the full
catalog without you doing anything.

## Importing the BrickLink catalog (themes & all sets)

The full BrickLink set taxonomy — themes, subthemes and every set in them —
can be imported so the Sets page browses like BrickLink's own catalog:

```bash
python -m brickonomy.catalog --tree              # category tree only (1 request)
python -m brickonomy.catalog --category 65       # one category's sets
python -m brickonomy.catalog --category 65 --deep  # + all its subcategories
python -m brickonomy.catalog --all               # the ENTIRE set catalog
```

`--all` walks every category page politely rate-limited — expect it to take a
long time; run it once and then keep it fresh with occasional `--category`
runs. Imported sets get name / year / theme / subtheme / category; prices are
fetched separately by `brickonomy.refresh` for the sets you care about.

## Minifigures

Every set page shows its exact minifig inventory (with real quantities,
scraped from the set's BrickLink inventory on refresh), each fig's value and
its share of the set's value. Each minifig has its own page — like BrickLink —
at `/sets/<fig id>` (e.g. `/sets/sw0879`): image, new/used value, price
history chart, best live prices, and an "appears in sets" cross-reference.

## Publish to GitHub Pages (the website link)

GitHub can't run the server, but it can host a **static snapshot** of the
whole site — every set page, minifig page, chart and the portfolio:

```bash
python -m brickonomy.export        # renders the site into docs/
git add docs && git commit -m "Update site" && git push
```

Then enable Pages once: repo **Settings → Pages → Deploy from a branch** →
branch **`main`**, folder **`/docs`**. The site appears at

> **https://rshiri.github.io/BrickLink-Price-Scraper-Analyzer/**

All links in the export are *relative*, so it also works if Pages is set to
the repo root (the root `index.html` redirects into `docs/`) or served from a
custom domain — no rebuild needed when the mount point changes.

Two files at the repo root matter for branch-source Pages and must not be
deleted: **`.nojekyll`** (without it GitHub runs Jekyll over the whole
repository — binaries, caches and all — and the build fails or mangles the
site) and **`index.html`** (redirects `/` into `docs/`). `.github/workflows/
pages-check.yml` probes the live URLs after each publish and fails loudly if
any of them stops returning 200.

Update loop: `python -m brickonomy.refresh` → `python -m brickonomy.export` →
commit + push. The published site is read-only (no refresh/import buttons);
prices are as of the last scan. Options: `--ccy USD` bakes a different display
currency, `--out <dir>` a different output folder.

## Pages

| Route | What it shows |
|---|---|
| `/` | Portfolio KPIs, top movers, best deals, most valuable / fastest growing / retiring soon, value by theme |
| `/sets` | Catalog with search, theme chips, sparklines, lifecycle status |
| `/sets/{id}` | Set page: values, buy signal, history + forecast chart (1Y/3Y/All, retirement marker), value vs retail, price per piece vs theme, best live prices, minifigs, parts inventory, related sets |
| `/sets/{fig}` | Minifig page: values, history, best prices, appears-in-sets |
| `/themes` | Theme analysis — totals, average growth, best performer |
| `/deals` | Bargain finder ranked by margin, filterable by rating |
| `/portfolio` | Owned sets with gain/loss, value-over-time chart, import/edit, wishlist |
| `/refresh` | Source health, scan trigger with live progress, configuration |

## Configuration

Copy `brickonomy/config.example.json` to `brickonomy/config.json` and edit, or
use `BRICKONOMY_*` environment variables (e.g. `BRICKONOMY_DISPLAY_CURRENCY=USD`,
`BRICKONOMY_SCRAPE_TTL_DAYS=1`). Keys: `display_currency`, `scrape_ttl_days`,
`request_delay_range`, `sources_enabled`, `rates_ttl_hours`, `fixture_mode`.

## Scrapers

Each scraper runs standalone and prints the normalized result + a
PriceAnalyzer summary:

```bash
python -m brickonomy.scrapers.ebay 75192
python -m brickonomy.scrapers.brickowl 75192
python -m brickonomy.scrapers.bricklink 75192
```

Flags: `--fixture` parses saved HTML from `brickonomy/tests/fixtures/`
(no network), `--save-fixture` stores freshly fetched HTML there — useful for
locking parser behavior into tests after eBay/BrickOwl markup changes.

Notes per source:

- **BrickLink** — wraps the root `scraper.py` (headless Chrome; prices in the
  session currency, ILS by default). Also fetches the set's full parts list
  and part-out value over plain HTTP.
- **eBay** — headless Chrome with stealth flags; the Buy-It-Now search only.
  Listings are title-classified into new/used and junk lots
  (instructions-only, minifig-only, bulk, clones) are filtered; titles run
  through PriceAnalyzer's completeness blacklist like any other description.
  **Sold/completed listings are no longer reachable**: eBay answers every
  anonymous `LH_Sold=1&LH_Complete=1` request with an error, sign-in or
  security page (five URL shapes checked — reproduce with
  `python -m brickonomy.doctor <set> --source ebay-sold`), so eBay counts as
  asks-only and PriceAnalyzer rates it LOW confidence.

## When a source stops returning anything

```bash
python -m brickonomy.doctor 75192              # all sources
python -m brickonomy.doctor 75192 --source ebay --dump
```

Prints, per source: HTTP status and page size, block/captcha detection, how
many rows each selector still matches, the page's most common CSS classes,
the text around each price, and what the parser then produced — enough to
tell "blocked" apart from "the markup changed" and from "this item really has
no offers". `--dump` writes the fetched HTML next to it for a new fixture.
`.github/workflows/scraper-doctor.yml` runs the same thing on a GitHub runner
when you want a clean IP for comparison.
- **BrickOwl** — plain HTTP; catalog search resolves the item page, whose
  "Buy" offers become current asks. BrickOwl has no public sold history, so
  its confidence is LOW by design.

Scrapers never raise: failures return an empty result with an `error` field,
the refresh records it, and the UI keeps serving the last snapshots.

## Tests

```bash
python -m pytest brickonomy/tests   # 60 tests, fully offline
```

## Layout

```
brickonomy/
  compat.py       bridge to the repo root's PriceAnalyzer / scraper / Database
  config.py       config.json + BRICKONOMY_* env
  db.py           brickonomy.db schema (append-only price_snapshots) + queries
  currency.py     frankfurter.app rates, cached + hardcoded fallbacks
  importer.py     seeds items/portfolio/snapshots from the repo's existing data
  refresh.py      multi-source refresh orchestrator (CLI + web job)
  snapshots.py    PriceAnalyzer results → snapshot rows
  analytics/      valuation (blend), growth, forecast, lifecycle
  scrapers/       base + bricklink / ebay / brickowl
  web/            FastAPI app, Jinja2 templates, Chart.js UI
  tests/          fixture-based parser tests + analytics/currency tests
```

The existing root scripts (`runner.py`, `dashboard.py`, …) are untouched and
keep working; Brickonomy only reads `bricklink_data.db`, never writes it.
