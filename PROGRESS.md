# Brickonomy Battle Plan — Progress

> Goal: beat BrickLink and BrickEconomy on **honesty** (sold-average values, confidence shown),
> **actionability** (what to buy / sell / part out), and **Israel** (landed cost).
> Full plan with rationale: https://claude.ai/code/artifact/1e4f0ae0-fd2c-4b8e-aaf0-0914ec1ddc0c

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

**Baseline (2026-08-30):** 203/17,367 minifigs priced · 590/16,930 sets priced ·
111 tests passing · site pushed, Pages not yet enabled.

---

## Wave 0 — Foundations
*A1 and A2 are independent; run in parallel. Nothing else starts before this wave passes QA.*

### [x] A1 · Scan Scheduler — 1 session — depends: nothing
Turn scanning into a nightly machine instead of a button.
- [x] `--scope priority` in `brickonomy/refresh.py` with tiers: owned → wishlist →
      figs of owned sets → retired sets in held themes → global gaps
      (target selection extracted to `select_targets()`, shared with `--dry-run`)
- [x] `scan_nightly.bat` + `schtasks` install line: nightly 02:00,
      `--scope priority --limit 150`, dated log in `logs/`, 14-day retention
- [x] OS-level file lock (`scan_lock()`), not a pid file — `os.kill(pid, 0)`
      *terminates* the process on Windows. Exit code 3 when another scan holds it.

**Accept:** ✅ `--dry-run` prints tiers in order (92 owned · 3 wishlist · 169 owned-figs ·
3,802 held-theme · 30,233 catalog) · ✅ 7 tier/lock tests · ✅ lock proven across processes ·
⏳ "one real night ≥100 items" pending the first scheduled run.

### [x] A2 · CI Medic — ½ session — depends: nothing
Defuse the workflows before they hurt us.
- [x] `catalog-refresh.yml`: restores `data/brickonomy.db` before rebuild, installs
      `beautifulsoup4`, rebases before push, commits the scanned db back,
      Monday cron commented out until proven
- [x] Inputs routed through `env:` in `theme-scan.yml` + `scraper-doctor.yml`
- [x] `pages-check.yml`: pinned to the `/docs` configuration; the impossible
      "`/` **and** `/docs/` both 200" pair replaced with real export URLs

**Accept:** ✅ all 4 workflows parse as YAML · ✅ every `run:` block passes `bash -n` ·
✅ no `${{ inputs }}` left in shell context (the one remaining is a boolean that
resolves to a literal flag) · ⏳ manual dispatch pending your trigger.

### [x] Wave 0 QA gate
- [x] Full suite green — **118 passed** (was 111; +7 scheduler tests)
- [x] Portfolio leak check green (7 lockbox tests)
- [x] Mobile sweep **n/a** — no CSS, template or JS file changed this wave

---

## Wave 1 — Coverage
*Starts once A1's scheduler runs. B1 ∥ B2.*

### [x] B1 · Fig Crawler — 1 session — depends: A1
- [x] `with_figs` on by default for `--scope priority`; `--with-figs` / `--no-figs`
      to override. A set's inventory is only discovered *while* scanning it, so
      figures found mid-run now join the same run instead of waiting a night.
- [x] No CLI queue cap — the `--limit` acts as one budget for the whole run,
      figures included, so a fig-heavy set can't silently triple a night
- [x] "Figs priced" column on the Refresh page, counted through the sets that
      contain them (figures carry no theme of their own)

**Accept:** ✅ 4 fig-expansion tests · ✅ column live (Marvel 162/168 = 96%, Icons 0/11) ·
⏳ "100% of owned-set figs" pending a week of nightly runs.

### [x] B2 · Theme Fleet — 1 session + your triggers — depends: A2
- [x] `python -m brickonomy.merge --rank-themes` — collected themes are a **tier**
      above uncollected ones, not a weight, so no amount of size lets an unowned
      theme jump the queue. Top now: City, Technic, Creator, Friends, Disney.
- [x] `brickonomy/merge.py`: read-only ATTACH + INSERT-merge, deduped on
      `(item_id, source, condition, kind, scraped_at)`. Portfolio and exchange
      rates are **never** merged — the runner's portfolio comes from the repo CSV.
- [x] Loop documented in `brickonomy/README.md`

**Accept:** ✅ 14 merge/ranking tests — zero dupes, zero loss, idempotent re-run,
portfolio untouched, incoming db unmodified · ⏳ one real theme round-trip pending
your workflow trigger.

### [x] Wave 1 QA gate
- [x] Full suite green — **136 passed** (was 118; +18)
- [x] Mobile: `/refresh` zero overflow at 375px **and** 320px with the new column
- [x] Portfolio leak check green

---

## Wave 2 — Data quality
*C1 ∥ C2 ∥ C3 (different files). Runs while Wave 1 scans in the background.*

### [x] C1 · Velocity — 1 session — depends: nothing
- [x] `analytics/velocity.py` + velocity chip on both value tiles; BrickLink only
      (eBay's sold count is a paginated signed-in search, so it measures the
      scrape, not the market). "No sold data" reads as unknown, never as zero.
- [x] LOW confidence dimmed and amber-chipped
- [x] Deals **ranked** on the liquidity-adjusted margin, not the paper one

**Accept:** ✅ 76051 shows ⇄ 10 sold/6mo new, 9 used · ✅ LOW visually distinct ·
✅ deals re-rank (10783: 262% → 184% adjusted at 3 sales/6mo) · ✅ 13 tests.

### [!] C2 · Retirement Dates — deferred by choice (option 3)
Every retirement year is `release + 2 or 3`, a flat guess. Rather than dress it
up, `lifecycle.phase()` now returns `retirement_estimated` and the set page says
so in plain words. Real dates still need a Brickset key.

<details><summary>Original plan</summary>
- [ ] Migration: `items.eol_date`, `items.availability`
- [ ] `brickonomy/brickset.py` batch fetch, heuristic fallback labeled "estimated"
- [ ] `lifecycle.py` + `forecast.py` consume real dates

**Accept:** ✅ estimates marked (done now) · ⏸ sourced dates need the key.
</details>

### [x] C3 · Snapshot Hygiene — ½ session — depends: nothing
- [x] `cleanup.py --compact`: collapses runs of 3+ identical consecutive prices,
      keeping first and last so both endpoints — when a price started and last
      held — survive
- [x] Index audit: `idx_snapshots_lookup` already covers the hot paths, and the
      scheduler's coverage probe runs as a COVERING INDEX scan. Nothing added.

**Accept:** ✅ **591/592 chart series byte-identical**; the one that changed kept
identical first/last values (removed points sat 15s and 3min apart at the same
price) · ✅ 7,837 → 7,825 rows · ✅ `latest_snapshot` ×400: 6ms → 5ms · ✅ 9 tests.

### [~] Wave 2 QA gate *(C2 still outstanding — needs your Brickset key)*
- [x] Full suite green — **159 passed** (was 136; +23)
- [x] Mobile: `/sets/76051` and `/deals` zero overflow at 375px **and** 320px
- [x] Portfolio leak check green

**Found and fixed en route:** the deals page was topped by BrickOwl listings at
₪0.03–0.06 for sets worth ₪1,000+ — millions of percent margin. BrickOwl matches
some set numbers against *parts* sharing the number (11211 is both a set and a
very common brick); 16 items affected. A plausibility floor now rejects any ask
under 2% of value. The scraper itself still stores the wrong prices — flagged as
a separate task.

---

## Wave 3 — Differentiators
*D2 ∥ D4 · D3 right after C1 (shared `deal_for()`) · D1 once coverage suffices.*

### [x] D1 · Part-Out Leaderboard — 2 sessions — depends: B1, C1
- [x] `/partout`: cheapest **believable** listing at landed cost vs part-out,
      velocity-discounted, fig share where figures are priced
- [x] Sort by adjusted margin / raw margin / profit / fig share; budget filter;
      stale-POV flag (>30d, flagged not dropped)
- [x] 10% selling fees **and** a 70% realisation rate — a part-out total is the
      theoretical maximum, not what reaches your pocket. Stated on the page.
- [x] "sell whole" flag where the intact set beats the part-out

**Accept:** ✅ ranks **357 sets, 230 profitable** · ✅ top-5 hand-checked (see below) ·
✅ no fee-mirage: yield is POV × 70% × 90%, and profit is measured against landed cost.

### [ ] D2 · Alert Runner — 1 session — depends: A1, **your Telegram bot token**
- [ ] `brickonomy/alerts.py` after each nightly scan: new deals, ±10%/30d portfolio moves,
      wishlist target hits
- [ ] Telegram delivery, token via env var (never committed); `alerts` table for dedupe

**Accept:** real message on your phone · zero repeats on re-run · thresholds in config.

### [ ] D3 · Landed-Cost Deals — ½ session — depends: C1
- [ ] `deal_for()` applies VAT + shipping to foreign-currency listings before margin
- [ ] Deal cards show ask **and** landed

**Accept:** the ₪1,437-landed eBay 76051 listing stops qualifying as a deal.

### [ ] D4 · Sell Signals — 1 session — depends: C1, C2
- [ ] "Consider selling" chip: flat 6-mo CAGR + above-median velocity + positive gain
- [ ] "Buy window closing" on wishlist items retiring inside 12 months
- [ ] Silence on thin evidence (<3 history points or LOW confidence)

**Accept:** flags the flat holdings only · tooltips state their numbers · none on unpriced rows.

### [~] Wave 3 QA gate *(D2/D3/D4 outstanding)*
- [x] Full suite green — **180 passed** (was 159; +21)
- [x] Mobile: `/partout` zero overflow at 375px **and** 320px
- [x] Portfolio leak check green

**Found and fixed en route.** Hand-checking the top rows showed 2 of 5 were not
sets at all: a *"LEGO Sticker Sheet for Set 5002145"* at ₪1.65, and a *"Black
Wheel Rim Ø14.6 x 9.9"* whose ₪14.60 "price" had been parsed out of the
**dimensions**. The 2%-of-value floor missed them because the sets themselves are
cheap. Listings whose description names a component are now rejected outright.
The top five are now BrickLink listings at 24–58% of set value with 2–30 sales
per 6 months.

`cheapest_stock` also moved from `web/app.py` into `analytics/listings.py` —
analytics importing from the web layer was backwards, and the deals finder and
the leaderboard need the same believability rules.

---

## Wave 4 — Polish
*Fully parallel, any order.*

### [ ] E1 · PWA — ½ session — depends: **Pages enabled**
Manifest + icons + service worker with a per-export version stamp.
**Accept:** installs on your phone · republish shows fresh prices within one reload.

### [ ] E2 · Theme Perf — ½ session — depends: nothing
One aggregate query replaces the per-row N+1; sets-only aggregates.
**Accept:** same numbers, <100 ms, figs no longer counted as sets.

### [ ] E3 · Compare — 1 session — depends: nothing
`/compare?ids=…` side-by-side; degrades honestly on unpriced items; works in the export.

### Wave 4 QA gate — [ ] suite · [ ] mobile · [ ] leak check · [ ] screenshots

---

## Only you can do these
- [ ] **Enable GitHub Pages** — Settings → Pages → branch `main`, folder `/docs` *(blocks E1)*
- [ ] **Brickset API key** — free at brickset.com/tools/webservices *(blocks C2)*
- [ ] **Telegram bot token** — @BotFather, ~2 minutes *(blocks D2; SMTP is the fallback)*
- [ ] **Trigger theme-scan runs** from the Actions tab when B2 hands you the ranked list
- [ ] **Portfolio password** set only at export time, never in a file

## Log
| Date | What happened |
|---|---|
| 2026-08-30 | Plan drawn; baseline recorded; nothing started |
| 2026-08-31 | **D1 complete.** `/partout` ranks 357 sets on landed cost vs a realistic part-out yield, velocity-discounted. Caught two non-set listings topping the board (a sticker sheet and a wheel rim whose price came from its dimensions) and added a component filter. Retirement dates now labelled estimated rather than deferred silently. Tests 159 → 180. |
| 2026-08-31 | **C1 + C3 complete.** Velocity surfaced from data already scraped but never shown; deals ranked on liquidity-adjusted margin; LOW confidence dimmed. Snapshot compaction with charts proven identical (591/592 byte-for-byte). Caught the deals page being topped by ₪0.03 phantom listings — BrickOwl matching parts to set numbers — and added a plausibility floor. Tests 136 → 159. |
| 2026-08-31 | **Wave 1 complete.** B1: figures discovered mid-scan join the same run; `--limit` is one budget for the whole night; "Figs priced" column per theme. B2: `merge.py` with read-only ATTACH, dedupe and hard exclusions for portfolio/rates; `--rank-themes` puts collected themes in their own tier. Tests 118 → 136. |
| 2026-08-31 | **Wave 0 complete.** A1: `--scope priority` with 5 tiers, `--dry-run`, cross-process scan lock, `scan_nightly.bat`. A2: all 4 workflows fixed — the Monday job that would have wiped the site is disabled and now restores the db first. Tests 111 → 118. Also fixed en route: catalog listings ordered priced-first, so `/minifigs` shows all 203 priced figs instead of 9. |
