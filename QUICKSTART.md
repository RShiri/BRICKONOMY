# Brickonomy — run it on your own PC

This guide assumes you have never used Python or a terminal. Five minutes,
two downloads, one double-click.

---

## 1. Install Python (once)

1. Go to **https://www.python.org/downloads/** and click the big yellow
   *Download Python* button.
2. Run the installer.
3. **Tick "Add python.exe to PATH"** on the very first screen — it is easy to
   miss, and nothing works without it.
4. Click *Install Now*, then *Close*.

You do not need to open Python. Ever.

## 2. Get the code

**Either** download the ZIP:

> https://github.com/RShiri/BrickLink-Price-Scraper-Analyzer/archive/refs/heads/main.zip

Right-click the downloaded file → *Extract All…* → pick somewhere easy like
`C:\Brickonomy`. Extract it properly; running from inside the ZIP will fail.

**Or**, if you have Git: `git clone https://github.com/RShiri/BrickLink-Price-Scraper-Analyzer.git`

## 3. Double-click `run.bat`

That is the whole installation. The first run takes a few minutes because it:

- builds a private Python environment in a `.venv` folder (nothing is installed
  system-wide, and deleting the folder removes every trace),
- downloads the libraries it needs,
- seeds your data from the files already in the repo (`bricklink_data.db` and
  the BrickEconomy CSV export),
- offers to download the **full LEGO catalog** — every set ever released,
  ~34,000 items, about a minute — so search works like BrickLink's,
- starts the website and opens your browser at **http://127.0.0.1:8000**.

Later runs skip all of that and start in a couple of seconds.

To stop it: press `Ctrl+C` in the black window, or just close the window.

> **macOS / Linux:** use `./run.sh` instead. The first time, run
> `chmod +x run.sh scan.sh` to make them executable.

---

## What you are looking at

| Page | What it is for |
|---|---|
| **Dashboard** (`/`) | What your collection is worth, today's movers, best deals, retiring soon |
| **Sets** | The whole catalog. Search by number, name or theme; filter by year, status |
| **Set page** | One set: current new/used value, buy signal, price history + forecast chart, value vs retail, price per piece, best live prices right now, its minifigures, its full parts list |
| **Minifig page** | Same idea for a single minifigure, plus which sets it appears in |
| **Themes** | Which themes hold value, average growth per year, best performer |
| **Deals** | Live listings priced below market value after fees, ranked by margin |
| **Portfolio** | Sets you own, what you paid, gain/loss, value over time, plus a wishlist |
| **Refresh & Sources** | Which sources are healthy, and the button that starts a scan |

## Getting prices

Set names and years come from the catalog import. **Prices only appear for
sets you have scanned** — until then a set shows "not scanned" and opens a
lightweight page with links out to BrickLink / BrickEconomy / eBay. That is
normal, not a bug.

Two ways to scan:

- In the website: **Refresh & Sources** → *Start scan* (progress shows live).
- Double-click **`scan.bat`** (`./scan.sh` on macOS/Linux) and pick:
  1. **My portfolio** — everything you own or want (start here)
  2. **Stale only** — just what has gone past the freshness window
  3. **One set** — type a set or minifig number
  4. **Everything** — the whole catalog; this runs for hours
  5. **Full LEGO catalog** — re-download names/years from Rebrickable
  6. **Rebuild the published site** into `docs/`

Scanning drives a real (invisible) Chrome for BrickLink and eBay and pauses a
few seconds between requests on purpose — being polite is what keeps the
scrapers from getting blocked. Budget roughly half a minute per set.

Want it to happen by itself? Windows *Task Scheduler* → run
`scan.bat` daily; on macOS/Linux add a cron line:

```
0 6 * * * cd /path/to/Brickonomy && .venv/bin/python -m brickonomy.refresh --scope stale
```

## Your portfolio

**Portfolio → Import** accepts a BrickEconomy CSV export, a BrickLink
wanted-list XML, or just a plain list of set numbers pasted in. You get a
preview before anything is saved. Rows can be edited inline afterwards —
quantity, what you paid, condition — and anything marked *wanted* moves to
the wishlist with live "best offer" pricing.

## Changing the currency

Top-right of the header: ILS / USD / EUR / GBP. Rates come from the European
Central Bank via frankfurter.app (no key, no account) and are cached, with
hardcoded fallbacks if you are offline. To make a choice permanent, copy
`brickonomy/config.example.json` to `brickonomy/config.json` and set
`"display_currency"`.

## Publishing your site

`scan.bat` option 6 (or `python -m brickonomy.export`) renders the whole site
as plain HTML into `docs/`. Commit and push it and GitHub Pages serves it at
your repo's Pages URL — read-only, prices as of your last scan.

---

## When something goes wrong

**"Python is not installed" although you installed it**
The *Add python.exe to PATH* box was not ticked. Re-run the installer,
choose *Modify*, and tick it — or just reinstall.

**The window flashes and disappears**
Open a Command Prompt in the folder (`Shift`+right-click → *Open PowerShell
window here*) and type `run.bat` so the error stays on screen.

**"Port 8000 is already in use"**
Something else is on that port. Run it on another one:

```
set PORT=8001
run.bat
```

**Nothing opens in the browser**
Type the address in yourself: http://127.0.0.1:8000

**Scanning fails on every BrickLink/eBay set**
Those two sources need **Google Chrome** installed. The matching driver is
fetched automatically — you do not need to install one. BrickOwl works
without Chrome.

**Windows Firewall asks for permission**
It is safe to allow (or to deny — the site only listens on `127.0.0.1`, your
own machine, and is not reachable from the network either way).

**You want a clean slate**
Delete `.venv` (the libraries) and/or `brickonomy.db` (your scanned prices and
portfolio), then run `run.bat` again. Deleting `brickonomy.db` loses your
price history, so export or back it up first if you care about it.

---

## Notes

- Everything runs locally. No account, no API key, no data leaves your PC
  except the requests to the shops themselves.
- `brickonomy.db` is the only file that holds your data. Copy it to back up.
- The original scripts in the repo root (`runner.py`, `dashboard.py`, …) still
  work exactly as before; Brickonomy only reads their database, never writes it.
- Growth and forecast figures are trend arithmetic on scraped prices, not
  financial advice.

Deeper documentation — architecture, scrapers, CLI flags, tests — is in
[`brickonomy/README.md`](brickonomy/README.md).
