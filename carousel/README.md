# Instagram carousel generator

Turns one set's market snapshot (a JSON payload) into a ready-to-post
Instagram carousel: 1080×1350 PNGs (4:5, the largest portrait format the
feed shows uncropped).

| Slide | Content |
| --- | --- |
| 1 · Hero | Set image, name / number / year / pieces / theme, MSRP vs current **new** and **used** value, month-over-month trend, growth vs retail, combined used value of all minifigures, "Prices valid as of …" stamp, watermark |
| 2 … N · Minifigures | Four figures per slide: image, BrickLink code, name, used price, ×quantity badge. Page counter, watermark. A free slot on the last slide becomes a call-to-action card |

The look is an Instagram post, not a web page: a story-style progress bar
along the top, a post header with the **brickanalyst.en** avatar in a
gradient story ring, the Instagram brand gradient (yellow → orange → pink →
purple) on the highlighted value card, pills and rank badges, dark glass
cards on near-black, and the handle with the Instagram glyph as watermark.
`assets/profile.jpg` is the avatar; a 1080×1080 copy for the Instagram
profile itself is in `out/profile-1080.jpg`.

Two designs ship, chosen with `--theme`:

| Theme | Look | Sample |
| --- | --- | --- |
| `ig` (default) | Instagram post: story progress bar, avatar in a gradient ring, glass cards, brand gradient highlights | [`out/76269/`](out/76269/) |
| `poster` | Flat toy-shelf poster: LEGO yellow band, white price tag with a punched hole, red used values, stud strip | [`out/76269-poster/`](out/76269-poster/) |

Sample output for **76269 Avengers Tower** is in those folders.
It was rendered on a machine that cannot reach `img.bricklink.com`, so the
pictures are the built-in placeholders; the site itself hotlinks the same
BrickLink URLs, and running the command below on a normal connection fills
in the real set and minifig images.

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
| `--theme ig\|poster` | Which design to render (see table above) |
| `--sort none\|value` | Keep payload order, or most valuable figure first |
| `--per-slide 1-4` | Figures per minifig slide (default and maximum 4) |
| `--scale 2` | Render at 2160×2700 for a crisper preview (Instagram downsizes anyway) |
| `--no-fetch` | Never touch the network: cached images or placeholders |
| `--keep-background` | Don't cut the white studio background out of photos |
| `--cache DIR` | Where downloaded images are kept (default `carousel/.cache`, git-ignored) |
| `--keep-html` | Write each slide's HTML next to its PNG (open it in a browser to tweak CSS live) |
| `--html-only` | Skip Chromium entirely; handy for template work and CI |

`manifest.json` lists the slides in order plus the rounded numbers that were
actually printed, so a posting script can build the caption from it.

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
- `templates/<theme>/hero.html` – slide 1.
- `templates/<theme>/minifigs.html` – the 2×2 figure grid.
- A new theme is a new folder with those three files; it shows up in
  `--theme` automatically.
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
can be started by hand from the Actions tab with any payload path, so a
machine that cannot download the pictures can still get finished slides.

## Tests

```bash
python -m pytest carousel/tests -q
```

They cover rounding, trend derivation, pagination and the HTML render;
no browser or network is needed.
