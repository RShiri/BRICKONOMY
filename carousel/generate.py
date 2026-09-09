#!/usr/bin/env python3
"""
Render a LEGO set's market snapshot into Instagram carousel slides.

    python -m carousel.generate carousel/samples/76269.json
    python -m carousel.generate payload.json --out out/76269 --sort value --keep-html

Slide 1 is the set "hero" (image, metadata, MSRP vs new/used, trend, minifig
total, timestamp); slides 2..N show the minifigures four per slide. Every
slide is 1080x1350 (4:5), the format Instagram keeps uncropped in the feed.

Pipeline: JSON payload -> normalise + round -> Jinja2 HTML (font, logo and
images inlined as data URIs, so Chromium never touches the network) ->
Playwright/Chromium screenshot -> PNG. HTML/CSS was chosen over Pillow because
the layout is ordinary CSS: change the look by editing carousel/templates/.

Requires: jinja2, requests, playwright (+ `playwright install chromium`).
`jsonschema` is optional; when installed the payload is validated first.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import mimetypes
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
THEMES = sorted(p.name for p in TEMPLATES.iterdir() if (p / "hero.html").exists())
DEFAULT_THEME = "poster"

# Units of each currency per 1 USD, used only when a payload in that currency
# gives no fx_rate of its own (mirrors brickonomy/currency.py's fallbacks).
FALLBACK_FX = {"USD": 1.0, "ILS": 3.65, "EUR": 0.92, "GBP": 0.78}
ASSETS = HERE / "assets"
SCHEMA = HERE / "schema.json"
DEFAULT_CACHE = HERE / ".cache"

WIDTH, HEIGHT = 1080, 1350          # Instagram portrait, 4:5 (CSS pixels)
DEFAULT_SCALE = 2                   # rendered at 2160x2700: crisper after Instagram's recompression
MAX_PER_SLIDE = 4
INSTAGRAM_MAX_SLIDES = 20

DEFAULT_BRANDING = {
    "site_name": "brickanalyst.en",
    "url": "",
    "handle": "@brickanalyst.en",
    "avatar": "assets/profile.jpg",
    "accent": "#ee2a7b",
    "gradient": "linear-gradient(135deg, #f9ce34 0%, #fa7e1e 28%, #ee2a7b 58%, #6228d7 100%)",
    "cta": "Full price history on the site",
}


# --------------------------------------------------------------------------- #
# Rounding rules                                                              #
# --------------------------------------------------------------------------- #
def round_dollar(x: float) -> int:
    """Nearest whole dollar, halves up (Python's round() is banker's)."""
    return int(math.floor(x + 0.5))


def round_to_5(x: float) -> int:
    """Nearest multiple of $5, so set prices end in 0 or 5 ($499.99 -> $500)."""
    return int(5 * math.floor(x / 5 + 0.5))


def fmt_money(n: int | float) -> str:
    """$1,275 – rendered with a small superscript '$' by the template."""
    return f'<span class="cur">$</span>{int(n):,}'


def fmt_thousands(n: int) -> str:
    return f"{int(n):,}"


# --------------------------------------------------------------------------- #
# Image fetching (with cache + placeholder fallback)                          #
# --------------------------------------------------------------------------- #
def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _sniff_mime(data: bytes, fallback: str = "image/png") -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.lstrip()[:5].lower() in (b"<svg ", b"<?xml"):
        return "image/svg+xml"
    return fallback


class ImageFetcher:
    """Resolve an image reference to a data URI.

    Accepts http(s) URLs, file:// URLs and local paths. Downloads are cached
    on disk by URL hash so re-rendering a set is free. Any failure (no
    network, 404, unexpected content) returns None and the caller draws a
    placeholder, so the pipeline never fails because of an image.
    """

    def __init__(self, cache_dir: Path, enabled: bool = True, timeout: float = 15.0,
                 cutout: bool = True):
        self.cache_dir = cache_dir
        self.enabled = enabled
        self.timeout = timeout
        self.cutout = cutout
        self.failures: list[str] = []
        cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, ref: str | None, base: Path | None = None) -> str | None:
        if not ref:
            return None
        parsed = urlparse(ref)
        try:
            if parsed.scheme in ("http", "https"):
                data = self._http(ref)
            elif parsed.scheme == "file":
                data = Path(parsed.path).read_bytes()
            else:
                p = Path(ref)
                if not p.is_absolute() and base is not None:
                    p = base / p
                data = p.read_bytes()
        except Exception as exc:  # noqa: BLE001 - any failure -> placeholder
            self.failures.append(f"{ref}: {exc.__class__.__name__}: {exc}")
            return None
        mime = mimetypes.guess_type(ref)[0] or ""
        if not mime.startswith("image/") or mime == "image/svg+xml":
            mime = _sniff_mime(data, mime or "image/png")
        if self.cutout and mime in ("image/png", "image/jpeg", "image/webp"):
            data, mime = cut_out_white_background(data)
        return _data_uri(data, mime)

    def _http(self, url: str) -> bytes:
        key = hashlib.sha1(url.encode()).hexdigest()
        cached = self.cache_dir / key
        if cached.exists():
            return cached.read_bytes()
        if not self.enabled:
            raise RuntimeError("fetching disabled (--no-fetch)")
        import requests  # local import: keep --no-fetch usable without it

        resp = requests.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 (brickonomy carousel generator)"},
        )
        resp.raise_for_status()
        data = resp.content
        if not _sniff_mime(data, "").startswith("image/"):
            raise ValueError("response is not an image")
        cached.write_bytes(data)
        return data


def bricklink_image_url(item_id: str, kind: str) -> str:
    """Default picture for an item, same convention the web app uses.

    BrickLink serves catalog images at a predictable URL, so payloads may omit
    `image_url` entirely: sets -> ItemImage/SN/0/<number>-1.png, minifigs ->
    ItemImage/MN/0/<code>.png.
    """
    if kind == "M":
        return f"https://img.bricklink.com/ItemImage/MN/0/{item_id}.png"
    suffix = item_id if "-" in item_id else f"{item_id}-1"
    return f"https://img.bricklink.com/ItemImage/SN/0/{suffix}.png"


def cut_out_white_background(data: bytes, threshold: int = 48, feather: int = 12) -> tuple[bytes, str]:
    """Make the white studio background of a catalog photo transparent.

    BrickLink (and most catalog) pictures sit on plain white, which shows as a
    white box on a dark slide. Steps:

    1. Flood-fill near-white from the image border. Only the region connected
       to the edge goes, so white *inside* the subject (eyes, prints,
       trans-clear parts) stays.
    2. In a `feather`-px band around that region, alpha follows brightness:
       pure white -> transparent, darker -> opaque. That fades the anti-aliased
       fringe and the soft drop shadow instead of leaving a hard white halo.
    3. A median filter on the band's alpha removes shadow speckles.

    Needs Pillow; without it the picture is returned unchanged.
    """
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except ImportError:
        return data, _sniff_mime(data)
    import io

    img = Image.open(io.BytesIO(data)).convert("RGBA")
    rgb = img.convert("RGB")
    w, h = rgb.size
    sentinel = (1, 255, 2)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    filled = False
    for xy in seeds:
        if all(c >= 255 - threshold for c in rgb.getpixel(xy)):
            ImageDraw.floodfill(rgb, xy, sentinel, thresh=threshold)
            filled = True
    if not filled:  # no white border: not a studio shot, leave it alone
        return data, _sniff_mime(data)

    # Background region (255 where flood-filled) and a feathered band around it.
    bg = ImageChops.difference(rgb, Image.new("RGB", (w, h), sentinel)).convert("L")
    bg = bg.point(lambda v: 255 if v == 0 else 0)
    band = bg.filter(ImageFilter.MaxFilter(2 * feather + 1))

    # Brightness -> alpha ramp: >= 250 transparent, <= 165 opaque. Off-white
    # shadow haze around a figure (JPEG photos) fades out instead of framing it.
    ramp = img.convert("L").point(lambda v: max(0, min(255, (250 - v) * 3)))
    ramp = ramp.filter(ImageFilter.MedianFilter(5))
    opaque = Image.new("L", (w, h), 255)
    alpha = Image.composite(ramp, opaque, band)          # ramp inside the band
    alpha = Image.composite(Image.new("L", (w, h), 0), alpha, bg)  # background fully clear
    alpha = ImageChops.multiply(img.getchannel("A"), alpha)

    out = img.copy()
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png"


def placeholder_minifig(code: str) -> str:
    """Inline SVG silhouette used when a minifig image can't be fetched."""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 260" width="200" height="260">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a3f4b"/><stop offset="1" stop-color="#22262e"/></linearGradient></defs>
<g fill="url(#g)" stroke="rgba(255,255,255,0.14)" stroke-width="2">
  <rect x="84" y="8" width="32" height="14" rx="5"/>
  <rect x="66" y="22" width="68" height="60" rx="16"/>
  <rect x="80" y="82" width="40" height="12" rx="4"/>
  <path d="M52 94h96l14 74H38z"/>
  <path d="M52 100l-26 62 18 6 24-58zM148 100l26 62-18 6-24-58z"/>
  <rect x="40" y="168" width="120" height="18" rx="5"/>
  <rect x="46" y="186" width="50" height="62" rx="6"/>
  <rect x="104" y="186" width="50" height="62" rx="6"/>
</g>
<circle cx="88" cy="48" r="4" fill="#0c0d10"/><circle cx="112" cy="48" r="4" fill="#0c0d10"/>
<path d="M88 62q12 10 24 0" stroke="#0c0d10" stroke-width="3" fill="none" stroke-linecap="round"/>
<text x="100" y="256" text-anchor="middle" font-family="monospace" font-size="15" fill="#7e8794">{code}</text>
</svg>"""
    return _data_uri(svg.encode(), "image/svg+xml")


def placeholder_set(number: str) -> str:
    """Inline SVG brick used when the set image can't be fetched."""
    studs = "".join(
        f'<ellipse cx="{70 + i * 60}" cy="{62 + j * 34}" rx="20" ry="9" fill="#2c313b" stroke="rgba(255,255,255,0.16)" stroke-width="2"/>'
        f'<rect x="{50 + i * 60}" y="{48 + j * 34}" width="40" height="14" fill="#2c313b"/>'
        f'<ellipse cx="{70 + i * 60}" cy="{48 + j * 34}" rx="20" ry="9" fill="#3a3f4b" stroke="rgba(255,255,255,0.2)" stroke-width="2"/>'
        for j in range(2) for i in range(4)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="-40 0 400 240" width="400" height="240">
<rect x="30" y="70" width="260" height="120" rx="12" fill="#262a33" stroke="rgba(255,255,255,0.16)" stroke-width="2"/>
<rect x="30" y="140" width="260" height="50" rx="12" fill="#1c2026"/>
{studs}
<text x="160" y="228" text-anchor="middle" font-family="monospace" font-size="20" fill="#7e8794">set {number} · image unavailable</text>
</svg>"""
    return _data_uri(svg.encode(), "image/svg+xml")


# --------------------------------------------------------------------------- #
# Payload -> view model                                                       #
# --------------------------------------------------------------------------- #
def validate(payload: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError:
        return  # optional
    schema = json.loads(SCHEMA.read_text())
    jsonschema.validate(payload, schema)


def parse_date(s: str) -> date:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return date.fromisoformat(s[:10])


def build_trend(market: dict[str, Any], new_value: float) -> dict[str, Any]:
    t = dict(market.get("trend") or {})
    prev = market.get("previous_new_value")
    if "direction" not in t and prev is not None:
        diff = new_value - prev
        pct = (diff / prev * 100) if prev else 0.0
        t = {"direction": "flat" if abs(pct) < 0.05 else ("up" if diff > 0 else "down"),
             "change_pct": pct, "change_usd": diff}
    elif "direction" not in t:
        t = {"direction": "flat"}
    t.setdefault("period", "vs last month")
    pct = t.get("change_pct")
    t["meter"] = min(abs(pct) * 10, 100) if pct is not None else 0
    if pct is not None:
        t["label"] = f"{abs(pct):.1f}%"
    elif t.get("change_usd") is not None:
        t["label"] = f"${abs(round_dollar(t['change_usd'])):,}"
    else:
        t["label"] = {"up": "rising", "down": "falling", "flat": "flat"}[t["direction"]]
    return t


def to_usd(pricing: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Return a copy of `pricing` in USD plus the rate that was applied.

    `currency` names the payload's currency (default USD) and `fx_rate` is how
    many of that currency buy one USD, e.g. 3.02 for ILS. Without an explicit
    rate a conservative built-in fallback is used and a warning is printed.
    """
    ccy = (pricing.get("currency") or "USD").upper()
    if ccy == "USD":
        return dict(pricing), 1.0
    fx = pricing.get("fx_rate")
    if not fx:
        fx = FALLBACK_FX.get(ccy)
        if fx is None:
            raise ValueError(f"unknown currency {ccy!r}: give pricing.fx_rate (units per 1 USD)")
        print(f"warning: no fx_rate for {ccy}; using fallback {fx} per USD", file=sys.stderr)
    out = {**pricing, "currency": "USD", "fx_rate": 1.0}
    for key in ("msrp", "new_value", "used_value", "minifigs_used_total"):
        if pricing.get(key) is not None:
            out[key] = pricing[key] / fx
    return out, float(fx)


def rarity_tier(pct: float, thresholds: tuple[float, float, float] = (3, 8, 15)) -> tuple[str, str]:
    """Map a percentage to a trading-card rarity tier: (css class, display label).

    Purely a decorative narrative device for the `card` theme, not a
    valuation claim. `thresholds` is (rare, epic, legendary) in percent;
    the default suits a per-minifig share-of-total (usually a few percent
    to ~20%). The hero card passes a wider set (vs retail growth, which
    ranges from deeply negative to 100%+).
    """
    lo, mid, hi = thresholds
    if pct >= hi:
        return "legendary", "Legendary"
    if pct >= mid:
        return "epic", "Epic"
    if pct >= lo:
        return "rare", "Rare"
    return "common", "Common"


def split_name(name: str) -> tuple[str, str]:
    """BrickLink names read 'Character - variant details'; show them on two lines."""
    head, sep, tail = name.partition(" - ")
    return (head.strip(), tail.strip()) if sep else (name.strip(), "")


def _asset_or_ref(ref: str | None, fetcher: ImageFetcher, base: Path) -> str | None:
    """Resolve a branding image: a bundled asset (relative to carousel/), a
    payload-relative path, or a URL. The avatar is used as-is (no cutout)."""
    if not ref:
        return None
    bundled = HERE / ref
    if bundled.exists():
        return _data_uri(bundled.read_bytes(), mimetypes.guess_type(str(bundled))[0] or "image/jpeg")
    cutout, fetcher.cutout = fetcher.cutout, False
    try:
        return fetcher.get(ref, base)
    finally:
        fetcher.cutout = cutout


def build_view(payload: dict[str, Any], fetcher: ImageFetcher, base: Path,
               sort: str, per_slide: int) -> dict[str, Any]:
    s, m = payload["set"], dict(payload["market"])
    branding = {**DEFAULT_BRANDING, **(payload.get("branding") or {})}

    # Everything below works in USD. Payloads may arrive in another currency
    # (the site stores ILS); convert once, up front, before any rounding.
    p, fx = to_usd(payload["pricing"])
    if fx != 1.0:
        if m.get("previous_new_value") is not None:
            m["previous_new_value"] = m["previous_new_value"] / fx
        if m.get("trend", {}).get("change_usd") is not None:
            m["trend"] = {**m["trend"], "change_usd": m["trend"]["change_usd"] / fx}

    figs = [dict(f) for f in payload["minifigs"]]
    for f in figs:
        f.setdefault("quantity", 1)
        f["used_price"] = f["used_price"] / fx
    if sort == "value":
        figs.sort(key=lambda f: -(f["used_price"] * f["quantity"]))

    raw_total = p.get("minifigs_used_total")
    if raw_total is None:
        raw_total = sum(f["used_price"] * f["quantity"] for f in figs)

    for rank, f in enumerate(figs, start=1):
        f["rank"] = rank
        f["title"], f["subtitle"] = split_name(f["name"])
        f["used_display"] = round_dollar(f["used_price"])
        url = f.get("image_url") or bricklink_image_url(f["code"], "M")
        f["image"] = fetcher.get(url, base) or placeholder_minifig(f["code"])

    pages = [figs[i:i + per_slide] for i in range(0, len(figs), per_slide)]

    msrp, new, used = round_to_5(p["msrp"]), round_to_5(p["new_value"]), round_to_5(p["used_value"])
    vs_retail = ((p["new_value"] - p["msrp"]) / p["msrp"] * 100) if p["msrp"] else 0.0
    used_vs_retail = ((p["used_value"] - p["msrp"]) / p["msrp"] * 100) if p["msrp"] else 0.0
    vs_retail_label = f"{'+' if vs_retail >= 0 else '−'}{abs(vs_retail):.0f}%"

    # Hook slide copy: a punchy lead-in line before the data-dense hero.
    # Any field left out of the payload falls back to a short stat headline
    # generated from the same numbers the burst badge already shows.
    hook_in = payload.get("hook") or {}
    hook = {
        "kicker": hook_in.get("kicker") or "Price check",
        "headline": hook_in.get("headline") or f"{vs_retail_label} since {s['year']}",
        "sub": hook_in.get("sub") or "Swipe for the full breakdown",
    }

    # Optional closing slide: a forward-looking price forecast. Only built
    # when the payload provides one -- unlike hook, there's no sensible
    # fallback for a number this speculative.
    forecast_in = payload.get("forecast")
    forecast = None
    if forecast_in:
        f_value_raw = forecast_in["value"] / fx
        change_pct = ((f_value_raw - p["new_value"]) / p["new_value"] * 100) if p["new_value"] else 0.0
        forecast = {
            "year": forecast_in["year"],
            "value_display": round_to_5(f_value_raw),
            "change_pct": change_pct,
            "change_label": f"{'+' if change_pct >= 0 else '−'}{abs(change_pct):.0f}%",
            "retire_note": forecast_in.get("retire_note", ""),
            "caption": forecast_in.get("caption", ""),
            "highlight": forecast_in.get("highlight", ""),
        }

    # Extra readouts: price per piece, the figures' combined value as a share
    # of the set's used value, and each figure's (quantity-weighted) slice of
    # that combined value (drives the little value bars).
    per_piece = (p["new_value"] / s["pieces"]) if s.get("pieces") else None
    fig_share = (raw_total / p["used_value"] * 100) if p["used_value"] else None
    for f in figs:
        slice_pct = (f["used_price"] * f["quantity"] / raw_total * 100) if raw_total else 0
        f["share_of_total"] = slice_pct
        f["share_label"] = f"{slice_pct:.1f}%" if slice_pct < 10 else f"{slice_pct:.0f}%"
        f["rarity_tier"], f["rarity_label"] = rarity_tier(slice_pct)

    return {
        "set": {**s, "number": str(s["number"])},
        "pricing": {
            "msrp_display": msrp, "new_display": new, "used_display": used,
            "vs_retail_pct": vs_retail,
            "vs_retail_label": vs_retail_label,
            "used_vs_retail_pct": used_vs_retail,
            "used_vs_retail_label": f"{'+' if used_vs_retail >= 0 else '−'}{abs(used_vs_retail):.0f}%",
            "per_piece_label": f"${per_piece:.2f}" if per_piece is not None else "—",
            # Meter widths (0-100) for themes with bars: growth capped at +100%,
            # month move at +-10%, $/piece against a $0.20 reference.
            "vs_retail_meter": min(abs(vs_retail), 100),
            "used_vs_retail_meter": min(abs(used_vs_retail), 100),
            "per_piece_meter": min(per_piece / 0.20 * 100, 100) if per_piece is not None else 0,
            # Rarity tier for the `card` theme, from growth since retail
            # (a much wider range than a minifig's share of the total, so it
            # gets its own thresholds).
            "rarity_tier": rarity_tier(vs_retail, thresholds=(0, 25, 75))[0],
            "rarity_label": rarity_tier(vs_retail, thresholds=(0, 25, 75))[1],
        },
        "market": {
            "updated_label": parse_date(m["updated_at"]).strftime("%b %-d, %Y"),
            "trend": build_trend(m, p["new_value"]),
        },
        "minifigs": {
            "figs": figs,
            "count": sum(f["quantity"] for f in figs),
            "unique": len(figs),
            "total_display": round_to_5(raw_total),
            "share_label": f"{fig_share:.0f}%" if fig_share is not None else "—",
            "share_meter": min(fig_share, 100) if fig_share is not None else 0,
            "set_used_display": used,
            "pages": pages,
        },
        "branding": branding,
        "hook": hook,
        "forecast": forecast,
        "fx_rate": fx,
        "assets": {
            "font": _data_uri((ASSETS / "fonts" / "Manrope-VariableFont_wght.ttf").read_bytes(), "font/ttf"),
            "font_hand": _data_uri((ASSETS / "fonts" / "PermanentMarker-Regular.ttf").read_bytes(), "font/ttf"),
            "mark": _data_uri((ASSETS / "brick-mark.svg").read_bytes(), "image/svg+xml"),
            "avatar": _asset_or_ref(branding.get("avatar"), fetcher, base),
            "set_image": fetcher.get(s.get("image_url") or bricklink_image_url(str(s["number"]), "S"), base)
            or placeholder_set(str(s["number"])),
        },
    }


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class Slide:
    index: int          # 1-based position in the carousel
    kind: str           # "hero" | "minifigs"
    html: str


def render_html(view: dict[str, Any], per_slide: int, theme: str = DEFAULT_THEME) -> list[Slide]:
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}; available: {', '.join(THEMES)}")
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES / theme)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["thousands"] = fmt_thousands
    # money() emits a <span>; mark it safe so autoescape leaves it alone.
    from markupsafe import Markup
    env.filters["money"] = lambda n: Markup(fmt_money(n))

    common = {k: view[k] for k in ("set", "pricing", "market", "minifigs", "branding", "assets")}
    pages = view["minifigs"]["pages"]
    # A theme may add its own hook.html for a bold, low-chrome lead-in slide
    # before the data-dense hero; themes without one just start on the hero,
    # same as before.
    has_hook = (TEMPLATES / theme / "hook.html").exists()
    offset = 1 if has_hook else 0
    # A theme may add its own forecast.html for a closing "looking ahead"
    # slide; it only appears when the payload also supplies a forecast
    # (there's no sensible fallback for a number this speculative).
    has_forecast = bool(view.get("forecast")) and (TEMPLATES / theme / "forecast.html").exists()
    total = 1 + offset + len(pages) + (1 if has_forecast else 0)

    # `nav` drives the story-style progress bar at the top of every slide.
    slides: list[Slide] = []
    if has_hook:
        slides.append(Slide(1, "hook", env.get_template("hook.html").render(
            nav={"index": 0, "total": total}, hook=view["hook"], **common)))
    slides.append(Slide(1 + offset, "hero", env.get_template("hero.html").render(
        nav={"index": offset, "total": total}, **common)))
    for i, items in enumerate(pages, start=1):
        page = {"index": i, "total": len(pages), "figs": items,
                "per_slide": per_slide, "is_last": i == len(pages),
                "first_rank": items[0]["rank"], "last_rank": items[-1]["rank"]}
        slides.append(Slide(1 + offset + i, "minifigs", env.get_template("minifigs.html").render(
            page=page, nav={"index": offset + i, "total": total}, **common)))
    if has_forecast:
        slides.append(Slide(len(slides) + 1, "forecast", env.get_template("forecast.html").render(
            nav={"index": total - 1, "total": total}, forecast=view["forecast"], **common)))
    return slides


def _optimize_png(path: Path) -> None:
    """Losslessly shrink Chromium's screenshot PNG (it writes them uncompressed-ish)."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(path) as im:
        im.save(path, format="PNG", optimize=True)


def screenshot(slides: list[Slide], out_dir: Path, stem: str, scale: int,
               chromium: str | None, keep_html: bool, fmt: str = "png", quality: int = 95) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    launch_kwargs: dict[str, Any] = {}
    if chromium:
        launch_kwargs["executable_path"] = chromium
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                                  device_scale_factor=scale)
        page = ctx.new_page()
        for slide in slides:
            name = f"{stem}-slide-{slide.index:02d}"
            if keep_html:
                (out_dir / f"{name}.html").write_text(slide.html, encoding="utf-8")
            page.set_content(slide.html, wait_until="load")
            page.evaluate("document.fonts.ready")
            target = out_dir / f"{name}.{fmt}"
            if fmt == "jpg":
                page.screenshot(path=str(target), type="jpeg", quality=quality,
                                clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
            else:
                page.screenshot(path=str(target), type="png",
                                clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
                _optimize_png(target)
            written.append(target)
        browser.close()
    return written


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload", type=Path, help="JSON file matching carousel/schema.json")
    ap.add_argument("--out", type=Path, help="output directory (default: carousel/out/<set number>)")
    ap.add_argument("--theme", choices=THEMES, default=DEFAULT_THEME,
                    help="visual design: 'poster' (LEGO yellow/red HUD, default) or 'ig' (Instagram gradient, glass cards)")
    ap.add_argument("--sort", choices=["none", "value"], default="none",
                    help="order minifigs as given, or most valuable first")
    ap.add_argument("--per-slide", type=int, default=MAX_PER_SLIDE, choices=[1, 2, 3, 4],
                    help="minifigs per slide (max 4)")
    ap.add_argument("--scale", type=int, default=DEFAULT_SCALE, choices=[1, 2, 3],
                    help="device scale factor: 1 = 1080x1350, 2 = 2160x2700 (default), 3 = 3240x4050")
    ap.add_argument("--format", choices=["png", "jpg"], default="png",
                    help="png (lossless, default) or jpg at --quality for smaller uploads")
    ap.add_argument("--quality", type=int, default=95, help="JPEG quality when --format jpg")
    ap.add_argument("--no-fetch", action="store_true",
                    help="never hit the network; use cached files or placeholders")
    ap.add_argument("--keep-background", action="store_true",
                    help="leave photo backgrounds as-is instead of cutting out the white studio background")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="image cache directory")
    ap.add_argument("--chromium", default=os.environ.get("CAROUSEL_CHROMIUM"),
                    help="path to a Chromium binary (else Playwright's own; env CAROUSEL_CHROMIUM)")
    ap.add_argument("--keep-html", action="store_true", help="also write each slide's HTML next to the PNG")
    ap.add_argument("--html-only", action="store_true", help="write HTML and skip Chromium (for template work)")
    args = ap.parse_args(argv)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    validate(payload)

    fetcher = ImageFetcher(args.cache, enabled=not args.no_fetch, cutout=not args.keep_background)
    view = build_view(payload, fetcher, args.payload.resolve().parent, args.sort, args.per_slide)
    slides = render_html(view, args.per_slide, args.theme)

    stem = view["set"]["number"]
    out_dir = args.out or (HERE / "out" / (stem if args.theme == DEFAULT_THEME else f"{stem}-{args.theme}"))

    if len(slides) > INSTAGRAM_MAX_SLIDES:
        print(f"warning: {len(slides)} slides exceeds Instagram's carousel limit of {INSTAGRAM_MAX_SLIDES}",
              file=sys.stderr)
    for failure in fetcher.failures:
        print(f"warning: image fallback -> placeholder: {failure}", file=sys.stderr)

    if args.html_only:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for s in slides:
            f = out_dir / f"{stem}-slide-{s.index:02d}.html"
            f.write_text(s.html, encoding="utf-8")
            files.append(f)
    else:
        files = screenshot(slides, out_dir, stem, args.scale, args.chromium, args.keep_html,
                           args.format, args.quality)

    manifest = {
        "set": view["set"]["number"],
        "name": view["set"]["name"],
        "theme": args.theme,
        "source_currency": (payload["pricing"].get("currency") or "USD").upper(),
        "fx_rate": view["fx_rate"],
        "size": [WIDTH * args.scale, HEIGHT * args.scale],
        "updated_at": payload["market"]["updated_at"],
        "displayed": {
            "msrp": view["pricing"]["msrp_display"],
            "new_value": view["pricing"]["new_display"],
            "used_value": view["pricing"]["used_display"],
            "minifigs_used_total": view["minifigs"]["total_display"],
            "trend": view["market"]["trend"]["direction"],
        },
        "slides": [{"index": s.index, "kind": s.kind, "file": f.name} for s, f in zip(slides, files)],
        "image_fallbacks": len(fetcher.failures),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for f in files:
        print(f)
    print(f"{len(files)} slides -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
