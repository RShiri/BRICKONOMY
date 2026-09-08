# Instagram carousel generator

Turns one set's market snapshot (a JSON payload) into a ready-to-post
Instagram carousel: 4:5 portrait slides (the largest format the feed shows
uncropped), rendered at **2160×2700** by default. Instagram scales feed
images down to 1080 wide, but a 2× source survives its recompression far
better than a 1080 one; `--scale 1` gives exact 1080×1350 files.

| Slide | Content |
| --- | --- |
| 1 · Hook *(themes with `hook.html`)* | Big set photo, a one-line headline meant to stop the scroll, a small kicker badge. Content, not a dashboard — the data lives on the next slide. Written per post via the `hook` field, or auto-generated from the pricing data when omitted |
| Hero | Set image, name / number / year / pieces / theme, MSRP vs current **new** and **used** value (each shown as a % vs retail), month-over-month trend, combined used value of all minifigures, "Prices valid as of …" stamp, watermark |
| Minifigures | Four figures per slide: image, BrickLink code, name, used price, ×quantity badge. Page counter, watermark. A free slot on the last slide becomes a call-to-action card |

The hook slide is opt-in per theme: a theme only gets one if its folder has
a `hook.html` (`poster` does; the rest currently start on the hero, as
before).

Every theme carries the same building blocks — a story-style progress bar,
a post header with the **brickanalyst.en** avatar, both new and used value
shown against retail, and a footer watermark — just drawn in a different
visual language. `assets/profile.jpg` is the avatar; a 1080×1080 copy for
the Instagram profile itself is in `out/profile-1080.jpg`.

Seven designs ship, chosen with `--theme`. `bento`, `contrast` and `card`
were built after a look at what's actually driving Instagram carousel
engagement in 2026 — see [Design research](#design-research-2026) below.
`marvel` and `dc` are the `poster` layout itself — the account's favorite —
recolored into each universe's own brand language rather than generic LEGO
yellow, for posting sets from that theme specifically.

| Theme | Look | Sample |
| --- | --- | --- |
| `poster` (default) | LEGO yellow and red, flat and bold: a hook slide up front, plain rounded panels with a stud dot in each corner and a stud strip along the footer (the one LEGO signature that stays), a white price tag, stat blocks (new vs retail, month move, $/piece, minifig share), a value bar per figure. No gradients, no glow, no grid | [`out/76269/`](out/76269/), [`out/76051/`](out/76051/) |
| `marvel` | The poster layout in Marvel's own colors: a red header band (not yellow), a halftone Ben-Day dot backdrop with comic speed lines instead of the tech grid, a black-stroked comic-ink headline, gold/red corner studs | [`out/76269-marvel/`](out/76269-marvel/), [`out/76051-marvel/`](out/76051-marvel/) |
| `dc` | The same layout in DC's colors: an electric-blue header band, a searchlight beam and tech-lattice backdrop, a soft blue energy glow on the headline instead of a comic-ink stroke, gold/blue corner studs | [`out/76269-dc/`](out/76269-dc/), [`out/76051-dc/`](out/76051-dc/) |
| `ig` | Instagram post: story progress bar, avatar in a gradient ring, glass cards, brand gradient highlights | [`out/76269-ig/`](out/76269-ig/), [`out/76051-ig/`](out/76051-ig/) |
| `bento` | Light mode — the only light theme of the set, which is the point of the contrast. Modular boxed tiles (2026's dominant layout trend), one accent color, no gradients or glow, generous whitespace | [`out/76269-bento/`](out/76269-bento/), [`out/76051-bento/`](out/76051-bento/) |
| `contrast` | A literal then/now split screen: muted release-era half (desaturated photo, retail price) against a vivid electric-blue "today" half (current value, trend), joined by a growth badge straddling the seam | [`out/76269-contrast/`](out/76269-contrast/), [`out/76051-contrast/`](out/76051-contrast/) |
| `card` | A collectible trading card: holographic corner foil, a metallic border colored by rarity tier, a rarity badge derived from the set's growth (or, per minifigure, its share of the figs total), card-stat rows, an edition number | [`out/76269-card/`](out/76269-card/), [`out/76051-card/`](out/76051-card/) |

`marvel` and `dc` always render negative/positive deltas (a fallen used
value, a down month) in a fixed alarm-red / green regardless of the
theme's own palette — a "down" reading should never quietly borrow the
brand's cool blue or gold and stop looking like bad news.

Sample output for **76269 Avengers Tower** and **76051 Super Hero Airport
Battle** is in those folders, in every theme.
It was rendered on a machine that cannot reach `img.bricklink.com`, so the
pictures are the built-in placeholders; the site itself hotlinks the same
BrickLink URLs, and running the command below on a normal connection fills
in the real set and minifig images.

## Design research (2026)

Before building `bento`, `contrast` and `card`, I looked at what's actually
driving engagement on Instagram carousels in 2026, rather than guessing:

- Carousels get 12% more engagement than Reels and 114% more than single
  images, and the highest save rate of any format (Socialinsider's 2026
  Instagram Benchmarks Report, 31M posts analyzed).
- **Bento grids** are the named successor to flat minimalism — modular
  boxed tiles mixing content types on one clean grid — and are described
  as the dominant 2026 UI layout trend, from product pages to social posts.
- **"Contrast carousels"** — two opposing ideas side by side, often a
  literal split-screen — are called out as a specific high-performing
  2026 format, especially for before/after and comparison content (exactly
  what a "retail vs today" price check is).
- General principles across every source: high contrast, bold typography,
  generous white space, and micro-learning (15–20 words a slide with one
  strong visual anchor) beat dense, paragraph-heavy slides.

`bento` and `contrast` are direct builds of the first two trends. `card`
isn't from the generic research — it leans on something a generic trend
report wouldn't know: LEGO collectors already speak fluent trading-card
visual language (rarity tiers, holo foil, card stats), so that genre fit
is a stronger hook for this specific audience than another generic
template would be.

## Why HTML/CSS + Chromium rather than Pillow

The whole look lives in three Jinja templates under `templates/` and is
ordinary CSS: flex/grid layout, `color-mix()`, gradients, drop shadows,
variable-font weights, text ellipsis. Restyling is a CSS edit; with Pillow
every one of those is hand-written pixel code. Chromium is driven by
Playwright, the same engine the repo already ships for browser tests.

## Setup

```bash
pip install -r carousel/requirements.txt
playwright install chromium          # once; ~150 MB
```

If you already have a Chromium/Chrome binary, skip the install and point
at it with `--chromium /path/to/chrome` or `CAROUSEL_CHROMIUM=…`.

## Usage

```bash
python -m carousel.generate carousel/samples/76269.json
#   -> carousel/out/76269/76269-slide-01.png … 76269-slide-08.png + manifest.json

python -m carousel.generate payload.json --out /tmp/post --sort value --keep-html
```

| Flag | Meaning |
| --- | --- |
| `--out DIR` | Output directory (default `carousel/out/<set number>`) |
| `--theme poster\|ig` | Which design to render (see table above) |
| `--sort none\|value` | Keep payload order, or most valuable figure first |
| `--per-slide 1-4` | Figures per minifig slide (default and maximum 4) |
| `--scale 1\|2\|3` | 1080×1350, 2160×2700 (default) or 3240×4050 |
| `--format png\|jpg` | Lossless PNG (default) or JPEG at `--quality` (95) for smaller uploads |
| `--no-fetch` | Never touch the network: cached images or placeholders |
| `--keep-background` | Don't cut the white studio background out of photos |
| `--cache DIR` | Where downloaded images are kept (default `carousel/.cache`, git-ignored) |
| `--keep-html` | Write each slide's HTML next to its PNG (open it in a browser to tweak CSS live) |
| `--html-only` | Skip Chromium entirely; handy for template work and CI |

`manifest.json` lists the slides in order plus the rounded numbers that were
actually printed, so a posting script can build the caption from it.

## Building a payload from the site export

`from_site.py` turns the static export under `docs/api/` into a payload:

```bash
python -m carousel.from_site 76051 --figs sh0177,sh0254,sh0255,sh0256,sh0257,sh0258 --msrp 79.99
python -m carousel.from_site 76051 --inventory-html brickonomy/tests/fixtures/bricklink_inv_figs_76051.html --msrp 79.99
```

It reads the set's `facts.json` (values, year, parts, theme) and
`history.json` (the value about a month earlier, for the trend), and looks
each minifig code up in `index.json` for its name and used value. Append
`:N` to a code for quantity (`sh0730:4`). `--msrp` is only needed when the
export has no retail price for the set. The payload is written in ILS with
the exchange rate the export itself implies.

## Input payload

The schema is [`schema.json`](schema.json) (JSON Schema 2020-12; validated
automatically when `jsonschema` is installed). A minimal payload:

```json
{
  "set":     { "number": "76269", "name": "Avengers Tower", "theme": "Marvel Super Heroes",
               "year": 2023, "pieces": 5201,
               "image_url": "https://img.bricklink.com/ItemImage/SN/0/76269-1.png" },
  "pricing": { "currency": "ILS", "fx_rate": 3.0193,
               "msrp": 1509.62, "new_value": 1531.86, "used_value": 1180.75 },
  "market":  { "updated_at": "2026-09-04", "previous_new_value": 1505.61 },
  "minifigs": [
    { "code": "sh0916", "name": "Vision - Dark Turquoise", "used_price": 145.94 },
    { "code": "sh0730", "name": "Chitauri - Dark Bluish Gray", "used_price": 15.21, "quantity": 4 }
  ],
  "branding": { "site_name": "brickanalyst.en", "handle": "@brickanalyst.en",
                "avatar": "assets/profile.jpg", "url": "",
                "cta": "Follow for weekly LEGO price checks" }
}
```

Notes:

- **Trend.** Give `market.previous_new_value` and the up/down arrow and
  percentage are derived; or give `market.trend` (`direction`, optional
  `change_pct` / `change_usd` / `period`) to state it yourself.
- **Minifig total.** `pricing.minifigs_used_total` is optional; when omitted
  it is the quantity-weighted sum of `minifigs[].used_price`.
- **Names.** BrickLink names ("Vision - Dark Turquoise") are split at the
  first " - " into a bold character line and a muted variant line.
- **Images.** `image_url` is optional: when omitted the generator uses
  BrickLink's catalog picture for the set number / minifig code, the same
  URLs the website hotlinks. It also accepts http(s), `file://` or a local
  path (relative to the payload). Downloads are cached; any failure
  (offline, 404, HTML instead of an image) falls back to a generated
  placeholder and is reported on stderr, so a missing picture never blocks
  a post. Catalog photos come on a white studio background; the generator
  flood-fills that from the edges to transparent so figures float on the
  dark cards (white inside the subject is untouched). `--keep-background`
  turns this off.
- **Branding.** `site_name` and `handle` default to brickanalyst.en.
  `avatar` is the profile picture in the story ring (bundled asset path,
  payload-relative path or URL; square works best). `url` adds an optional
  website line under the handle. `gradient` (any CSS gradient) recolours the
  progress bar, rings, highlight card and gradient text; `accent` is the
  matching solid colour. Both default to Instagram's brand colours.
- **Hook slide.** Optional `hook` object — `kicker`, `headline`, `sub` —
  for the lead-in slide on themes that have one. Write your own per post
  (e.g. `"headline": "When will the Avengers Tower retire?"`) or leave any
  field out and it's generated from the pricing data, e.g. `"+1% since
  2023"`.

## Currency

Slides always show USD. The payload may be in another currency: set
`pricing.currency` (`USD`, `ILS`, `EUR`, `GBP`) and `pricing.fx_rate`, the
number of that currency per 1 USD. Every money field is converted before
rounding: MSRP, new and used value, the minifig total, last month's value,
and each minifig's used price. The Brickonomy site stores values in ILS,
so its exports need `"currency": "ILS"` plus the rate; the sample uses
3.0193, the rate implied by the site's own retail conversion. Without an
`fx_rate` a built-in fallback rate is used and a warning is printed.
`manifest.json` records the source currency and the rate applied.

## Rounding

Displayed values are rounded for the feed; the payload keeps the raw numbers.

| Value | Rule | Example |
| --- | --- | --- |
| MSRP, new value, used value, minifig total | nearest **$5** (ends in 0 or 5) | $499.99 → $500, $507.36 → $505 |
| Each minifigure | nearest **$1** | $48.34 → $48, $5.04 → $5 |

## Customising the look

- `templates/<theme>/base.html` – shell shared by every slide of that theme:
  palette tokens, progress bar, header, footer/watermark.
- `templates/<theme>/hero.html` – the hero slide.
- `templates/<theme>/minifigs.html` – the 2×2 figure grid.
- `templates/<theme>/hook.html` *(optional)* – the lead-in slide, rendered
  first when present.
- A new theme is a new folder with `base.html`, `hero.html` and
  `minifigs.html`; it shows up in `--theme` automatically. Add `hook.html`
  too if it should open on a hook slide.
- `assets/fonts/` – Manrope (SIL OFL), embedded so output is identical on any machine.
- `assets/profile.jpg` – the avatar; `assets/brick-mark.svg` is the fallback
  when a payload sets `avatar` to an empty string.

Run with `--keep-html`, open the HTML in a browser at 1080×1350 and iterate
on the CSS; re-run to regenerate the PNGs.

## Rendering in GitHub Actions

`.github/workflows/carousel.yml` renders a payload on a GitHub runner (which
can reach BrickLink), uploads the slides as a workflow artifact and commits
them under `carousel/out/<set>/` on the branch it ran from. It runs on
every push that touches `carousel/` (except the rendered output itself) and
can be started by hand from the Actions tab with any payload path (or none,
to render every file in `samples/`), so a machine that cannot download the
pictures can still get finished slides.

## Tests

```bash
python -m pytest carousel/tests -q
```

They cover rounding, trend derivation, pagination and the HTML render;
no browser or network is needed.
