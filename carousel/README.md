# Instagram carousel generator

Turns one set's market snapshot (a JSON payload) into a ready-to-post
Instagram carousel: 1080×1350 PNGs (4:5, the largest portrait format the
feed shows uncropped).

| Slide | Content |
| --- | --- |
| 1 · Hero | Set image, name / number / year / pieces / theme, MSRP vs current **new** and **used** value, month-over-month trend, growth vs retail, combined used value of all minifigures, "Prices valid as of …" stamp, watermark |
| 2 … N · Minifigures | Four figures per slide: image, BrickLink code, name, used price, ×quantity badge. Page counter, watermark. A free slot on the last slide becomes a call-to-action card |

The look is Instagram-native rather than the web app's: dark glass cards on
near-black, the Instagram brand gradient (yellow → orange → pink → purple)
for the story-style progress bar along the top, the "story ring" around
every image, the highlighted value card and the handle line with the
Instagram glyph.

Sample output for **76269 Avengers Tower** is in [`out/76269/`](out/76269/).
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
| `--sort none\|value` | Keep payload order, or most valuable figure first |
| `--per-slide 1-4` | Figures per minifig slide (default and maximum 4) |
| `--scale 2` | Render at 2160×2700 for a crisper preview (Instagram downsizes anyway) |
| `--no-fetch` | Never touch the network: cached images or placeholders |
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
  "pricing": { "currency": "USD", "msrp": 499.99, "new_value": 1273.65, "used_value": 1018.40 },
  "market":  { "updated_at": "2026-09-08", "previous_new_value": 1224.90 },
  "minifigs": [
    { "code": "sh0916", "name": "Vision - Dark Turquoise", "used_price": 137.14,
      "image_url": "https://img.bricklink.com/ItemImage/MN/0/sh0916.png" },
    { "code": "sh0730", "name": "Chitauri - Dark Bluish Gray", "used_price": 5.09, "quantity": 4,
      "image_url": "https://img.bricklink.com/ItemImage/MN/0/sh0730.png" }
  ],
  "branding": { "site_name": "BRICKONOMY", "url": "rshiri.github.io/BRICKONOMY",
                "handle": "@brickonomy", "logo": "", "accent": "#d7ff3a",
                "cta": "Full price history on the site" }
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
  a post.
- **Branding.** `branding.logo` (SVG/PNG, light-on-dark) replaces the
  default brick mark + wordmark. `gradient` (any CSS gradient) recolours the
  progress bar, rings, highlight card and gradient text; `accent` is the
  matching solid colour. Both default to Instagram's brand colours.

## Rounding

Displayed values are rounded for the feed; the payload keeps the raw numbers.

| Value | Rule | Example |
| --- | --- | --- |
| MSRP, new value, used value, minifig total | nearest **$5** (ends in 0 or 5) | $499.99 → $500, $1,273.65 → $1,275 |
| Each minifigure | nearest **$1** | $137.14 → $137, $5.09 → $5 |

## Customising the look

- `templates/base.html` – shell shared by every slide: palette and gradient
  tokens, story progress bar, header, footer/watermark.
- `templates/hero.html` – slide 1.
- `templates/minifigs.html` – the 2×2 figure grid.
- `assets/fonts/` – Manrope (SIL OFL), embedded so output is identical on any machine.
- `assets/brick-mark.svg` – default logo mark; swap it or set `branding.logo`.

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
