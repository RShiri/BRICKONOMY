#!/usr/bin/env python3
"""
Build a carousel payload for one set from the site's static export (docs/api).

    python -m carousel.from_site 76051 --figs sh0177,sh0254,sh0255,sh0256,sh0257,sh0258 --msrp 79.99
    python -m carousel.from_site 76051 --inventory-html brickonomy/tests/fixtures/bricklink_inv_figs_76051.html --msrp 79.99

Reads docs/api/sets/<set>/facts.json (values, year, parts, theme),
docs/api/sets/<set>/history.json (the value about a month ago, for the
trend) and docs/api/index.json (minifig names and used values). Everything in
the export is ILS; the payload is written in ILS with the fx_rate the export
itself implies, and carousel/generate.py converts to USD when rendering.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "docs" / "api"
HERE = Path(__file__).resolve().parent

THEME_NAMES = {"Super Heroes Marvel": "Marvel Super Heroes", "Super Heroes DC": "DC Super Heroes"}


def implied_fx_rate() -> float | None:
    """ILS per USD, from any set whose facts carry both a USD retail and its ILS conversion."""
    for facts in API.glob("sets/*/facts.json"):
        f = json.loads(facts.read_text())
        if f.get("retail_native") and f.get("retail") and f.get("retail_ccy") == "USD":
            return f["retail"] / f["retail_native"]
    return None


def figs_from_inventory_html(path: Path) -> list[tuple[str, int]]:
    """(code, quantity) pairs from a saved BrickLink 'minifigs in set' inventory page."""
    text = re.sub(r"<[^>]+>", " ", path.read_text(errors="ignore"))
    text = re.sub(r"&nbsp;|\s+", " ", text)
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"(\d+)\s+([a-z]{2,4}\d{3,4}[a-z]?)\s+\( Inv \)", text):
        out.append((m.group(2), int(m.group(1))))
    return out


def build(set_number: str, figs: list[tuple[str, int]], msrp_usd: float | None,
          fx: float, updated: str | None) -> dict:
    facts = json.loads((API / "sets" / set_number / "facts.json").read_text())
    history = json.loads((API / "sets" / set_number / "history.json").read_text())
    index = json.loads((API / "index.json").read_text())
    rows = {r[0]: r for r in index["rows"]}
    if facts.get("currency") != "ILS":
        sys.exit(f"expected an ILS export, got {facts.get('currency')}")

    series = sorted(history["series"]["blended"], key=lambda p: p["t"])
    latest = series[-1]
    cutoff = (date.fromisoformat(latest["t"]) - timedelta(days=25)).isoformat()
    earlier = [p for p in series if p["t"] <= cutoff]
    previous = earlier[-1]["v"] if earlier else series[0]["v"]

    minifigs = []
    for code, qty in figs:
        row = rows.get(code)
        if not row or row[5] != "M":
            print(f"skip {code}: not a minifig in the export", file=sys.stderr)
            continue
        used = row[8] or row[7]
        if not used:
            print(f"skip {code}: no value in the export", file=sys.stderr)
            continue
        f = {"code": code, "name": row[1], "used_price": used}
        if qty > 1:
            f["quantity"] = qty
        minifigs.append(f)
    minifigs.sort(key=lambda f: -f["used_price"] * f.get("quantity", 1))

    msrp_ils = facts.get("retail") or (msrp_usd * fx if msrp_usd else None)
    if msrp_ils is None:
        sys.exit("the export has no retail price for this set: pass --msrp <USD>")

    return {
        "set": {
            "number": set_number, "name": facts["name"],
            "theme": THEME_NAMES.get(facts.get("theme", ""), facts.get("theme", "")),
            "year": facts["year"], "pieces": facts["parts"],
        },
        "pricing": {"currency": "ILS", "fx_rate": round(fx, 4), "msrp": round(msrp_ils, 2),
                    "new_value": facts["value_new"], "used_value": facts["value_used"]},
        "market": {"updated_at": updated or latest["t"], "previous_new_value": previous},
        "minifigs": minifigs,
        "branding": {"site_name": "brickanalyst.en", "handle": "@brickanalyst.en",
                     "avatar": "assets/profile.jpg", "cta": "Follow for weekly LEGO price checks"},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", help="set number, e.g. 76051")
    ap.add_argument("--figs", help="comma-separated minifig codes (append :N for quantity, e.g. sh0730:4)")
    ap.add_argument("--inventory-html", type=Path, help="saved BrickLink minifig inventory page to read codes from")
    ap.add_argument("--msrp", type=float, help="retail price in USD when the export has none")
    ap.add_argument("--fx", type=float, help="ILS per USD (default: implied by the export, else 3.0193)")
    ap.add_argument("--updated", help="override the 'prices valid as of' date (ISO)")
    ap.add_argument("--out", type=Path, help="where to write (default carousel/samples/<set>.json)")
    args = ap.parse_args(argv)

    figs: list[tuple[str, int]] = []
    if args.inventory_html:
        figs += figs_from_inventory_html(args.inventory_html)
    if args.figs:
        for item in args.figs.split(","):
            code, _, qty = item.strip().partition(":")
            figs.append((code, int(qty) if qty else 1))
    if not figs:
        sys.exit("give --figs or --inventory-html")

    fx = args.fx or implied_fx_rate() or 3.0193
    payload = build(args.set, figs, args.msrp, fx, args.updated)
    out = args.out or (HERE / "samples" / f"{args.set}.json")
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(payload['minifigs'])} minifigs, fx {fx:.4f}, new {payload['pricing']['new_value']} ILS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
