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

### [ ] C2 · Retirement Dates — 1 session — depends: **your Brickset API key**
- [ ] Migration: `items.eol_date`, `items.availability`
- [ ] `brickonomy/brickset.py` batch fetch, heuristic fallback labeled "estimated"
- [ ] `lifecycle.py` + `forecast.py` consume real dates

**Accept:** owned sets show sourced dates · estimates marked · forecast tests green.

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

### [ ] D1 · Part-Out Leaderboard — 2 sessions — depends: B1, C1
- [ ] `/partout` page: cheapest live listing at **landed** cost vs POV, fig/parts split,
      velocity-discounted margin
- [ ] Sort by margin / profit / fig-share; budget filter
- [ ] Fees subtracted (13% allowance exists); stale POVs (>30d) flagged

**Accept:** ranks ≥300 sets · top-3 hand-verified against live listings · no fee-mirage profits.

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

### Wave 3 QA gate — [ ] suite · [ ] mobile · [ ] leak check · [ ] screenshots

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
| 2026-08-31 | **C1 + C3 complete.** Velocity surfaced from data already scraped but never shown; deals ranked on liquidity-adjusted margin; LOW confidence dimmed. Snapshot compaction with charts proven identical (591/592 byte-for-byte). Caught the deals page being topped by ₪0.03 phantom listings — BrickOwl matching parts to set numbers — and added a plausibility floor. Tests 136 → 159. |
| 2026-08-31 | **Wave 1 complete.** B1: figures discovered mid-scan join the same run; `--limit` is one budget for the whole night; "Figs priced" column per theme. B2: `merge.py` with read-only ATTACH, dedupe and hard exclusions for portfolio/rates; `--rank-themes` puts collected themes in their own tier. Tests 118 → 136. |
| 2026-08-31 | **Wave 0 complete.** A1: `--scope priority` with 5 tiers, `--dry-run`, cross-process scan lock, `scan_nightly.bat`. A2: all 4 workflows fixed — the Monday job that would have wiped the site is disabled and now restores the db first. Tests 111 → 118. Also fixed en route: catalog listings ordered priced-first, so `/minifigs` shows all 203 priced figs instead of 9. |
