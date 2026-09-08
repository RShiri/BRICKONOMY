"""Fast checks for the carousel generator: no browser, no network."""
import json
from pathlib import Path

import pytest

from carousel import generate as g

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "76269.json"


def test_rounding_rules():
    # Set prices land on 0/5; minifig prices on whole dollars.
    assert g.round_to_5(499.99) == 500
    assert g.round_to_5(1273.65) == 1275
    assert g.round_to_5(1018.40) == 1020
    assert g.round_to_5(917.49) == 915
    assert g.round_to_5(917.50) == 920
    assert g.round_dollar(137.14) == 137
    assert g.round_dollar(5.5) == 6
    assert g.round_dollar(20.49) == 20


def test_trend_is_derived_from_previous_value():
    up = g.build_trend({"previous_new_value": 1224.9}, 1273.65)
    assert up["direction"] == "up" and up["label"] == "4.0%" and up["period"] == "vs last month"
    down = g.build_trend({"previous_new_value": 1300}, 1273.65)
    assert down["direction"] == "down" and down["label"] == "2.0%"
    explicit = g.build_trend({"trend": {"direction": "down", "change_pct": -7.5, "period": "vs Aug"}}, 100)
    assert explicit["label"] == "7.5%" and explicit["period"] == "vs Aug"
    assert g.build_trend({}, 100)["direction"] == "flat"


def test_default_image_urls_follow_bricklink_convention():
    assert g.bricklink_image_url("76269", "S") == "https://img.bricklink.com/ItemImage/SN/0/76269-1.png"
    assert g.bricklink_image_url("76269-1", "S") == "https://img.bricklink.com/ItemImage/SN/0/76269-1.png"
    assert g.bricklink_image_url("sh0916", "M") == "https://img.bricklink.com/ItemImage/MN/0/sh0916.png"


def test_white_background_is_cut_out_but_inner_white_is_kept(tmp_path):
    from PIL import Image
    import io
    img = Image.new("RGB", (60, 60), (255, 255, 255))
    for x in range(15, 45):
        for y in range(15, 45):
            img.putpixel((x, y), (200, 30, 30))          # red subject
    img.putpixel((30, 30), (255, 255, 255))               # white "eye" inside it
    for y in range(15, 45):
        img.putpixel((14, y), (230, 200, 200))            # anti-aliased fringe pixel
    img.putpixel((5, 52), (225, 225, 225))                # lone shadow speckle
    buf = io.BytesIO(); img.save(buf, format="PNG")
    data, mime = g.cut_out_white_background(buf.getvalue())
    out = Image.open(io.BytesIO(data))
    assert mime == "image/png" and out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0                  # background gone
    assert out.getpixel((20, 20))[3] == 255              # subject kept
    assert out.getpixel((30, 30)) == (255, 255, 255, 255)  # enclosed white kept
    assert 0 < out.getpixel((14, 30))[3] < 255           # fringe faded, not clipped
    assert out.getpixel((5, 52))[3] == 0                 # speckle removed

    # A photo with no white border is left untouched.
    dark = Image.new("RGB", (20, 20), (30, 30, 30)); b2 = io.BytesIO(); dark.save(b2, format="PNG")
    assert g.cut_out_white_background(b2.getvalue())[0] == b2.getvalue()

    # A local file goes through the same path via the fetcher.
    f = tmp_path / "fig.png"; f.write_bytes(buf.getvalue())
    uri = g.ImageFetcher(tmp_path / "cache", enabled=False).get(str(f))
    assert uri.startswith("data:image/png;base64,")


def test_payload_currency_is_converted_to_usd():
    p, fx = g.to_usd({"currency": "ILS", "fx_rate": 3.0193, "msrp": 1509.62, "new_value": 1531.86, "used_value": 1180.75})
    assert fx == 3.0193 and p["currency"] == "USD"
    assert round(p["msrp"], 2) == 499.99 and round(p["new_value"], 2) == 507.36
    p, fx = g.to_usd({"msrp": 10, "new_value": 20, "used_value": 15})
    assert fx == 1.0 and p["msrp"] == 10
    with pytest.raises(ValueError):
        g.to_usd({"currency": "XXX", "msrp": 1, "new_value": 1, "used_value": 1})


def test_split_name():
    assert g.split_name("Vision - Dark Turquoise") == ("Vision", "Dark Turquoise")
    assert g.split_name("Kevin Feige") == ("Kevin Feige", "")


@pytest.fixture
def view(tmp_path):
    payload = json.loads(SAMPLE.read_text())
    fetcher = g.ImageFetcher(tmp_path / "cache", enabled=False)
    return payload, g.build_view(payload, fetcher, SAMPLE.parent, sort="none", per_slide=4), fetcher


def test_view_paginates_four_per_slide_and_weights_quantity(view):
    payload, v, fetcher = view
    figs = payload["minifigs"]
    pages = v["minifigs"]["pages"]
    assert len(pages) == -(-len(figs) // 4)
    assert all(len(p) <= 4 for p in pages)
    assert v["minifigs"]["unique"] == len(figs)
    assert v["minifigs"]["count"] == sum(f.get("quantity", 1) for f in figs)
    raw = sum(f["used_price"] * f.get("quantity", 1) for f in figs) / 3.0193
    assert v["minifigs"]["total_display"] == g.round_to_5(raw)
    # Sample is in ILS (3.0193 per USD): 1531.86 -> $507 -> $505, 1180.75 -> $391 -> $390
    assert v["pricing"]["msrp_display"] == 500
    assert v["pricing"]["new_display"] == 505
    assert v["pricing"]["used_display"] == 390
    assert v["pricing"]["used_vs_retail_label"] == "−22%"   # used $391 vs $500 retail
    assert round(v["pricing"]["used_vs_retail_pct"]) == -22
    assert v["market"]["trend"]["direction"] == "up" and v["market"]["trend"]["label"] == "1.7%"
    assert v["minifigs"]["figs"][0]["used_display"] == 48      # Vision 145.94 ILS
    shares = [f["share_of_total"] for f in v["minifigs"]["figs"]]
    assert round(sum(shares)) == 100 and v["minifigs"]["figs"][0]["share_label"] == "17%"
    assert v["minifigs"]["share_label"] == "72%" and v["minifigs"]["set_used_display"] == 390
    assert v["market"]["updated_label"] == "Sep 4, 2026"
    # Offline: every image became a placeholder rather than an error.
    assert len(fetcher.failures) == len(figs) + 1
    assert v["assets"]["set_image"].startswith("data:image/svg+xml")


def test_html_renders_every_slide(view):
    _, v, _ = view
    slides = g.render_html(v, per_slide=4, theme="ig")
    assert slides[0].kind == "hero" and all(s.kind == "minifigs" for s in slides[1:])
    assert len(slides) == 1 + len(v["minifigs"]["pages"])
    hero = slides[0].html
    assert "Avengers Tower" in hero and ">505<" in hero and "Prices valid as of" in hero
    assert "1.7% vs last month" in hero
    figs = "".join(s.html for s in slides[1:])
    assert figs.count('class="qty">×4') == 1     # the Chitauri card, once
    assert "Minifigs <span" in slides[-1].html
    assert f"{len(slides) - 1}/{len(slides) - 1}" in slides[-1].html


def test_every_theme_renders(view):
    _, v, _ = view
    assert set(g.THEMES) >= {"ig", "poster"}
    for theme in g.THEMES:
        slides = g.render_html(v, per_slide=4, theme=theme)
        assert len(slides) == 8 and all("brickanalyst.en" in s.html for s in slides)
    with pytest.raises(ValueError):
        g.render_html(v, per_slide=4, theme="nope")


def test_from_site_builds_76051(tmp_path):
    from carousel import from_site
    out = tmp_path / "76051.json"
    assert from_site.main(["76051", "--figs", "sh0177,sh0254,sh0255,sh0256,sh0257,sh0258",
                           "--msrp", "79.99", "--out", str(out)]) == 0
    p = json.loads(out.read_text())
    assert p["set"]["name"] == "Super Hero Airport Battle" and p["set"]["pieces"] == 807
    assert p["pricing"]["currency"] == "ILS" and p["pricing"]["fx_rate"] == 3.0193
    assert round(p["pricing"]["msrp"] / p["pricing"]["fx_rate"], 2) == 79.99
    assert [f["code"] for f in p["minifigs"]][:2] == ["sh0254", "sh0258"]   # most valuable first
    assert p["market"]["previous_new_value"] == 461.6
    html = Path("brickonomy/tests/fixtures/bricklink_inv_figs_76051.html")
    if html.exists():
        assert dict(from_site.figs_from_inventory_html(html))["sh0254"] == 1


def test_cli_html_only(tmp_path):
    out = tmp_path / "out"
    assert g.main([str(SAMPLE), "--out", str(out), "--no-fetch", "--html-only",
                   "--cache", str(tmp_path / "cache")]) == 0
    files = sorted(out.glob("76269-slide-*.html"))
    assert len(files) == 8
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["displayed"] == {"msrp": 500, "new_value": 505, "used_value": 390,
                                     "minifigs_used_total": 285, "trend": "up"}
    assert manifest["theme"] == "poster"
    assert manifest["source_currency"] == "ILS" and manifest["fx_rate"] == 3.0193
    assert manifest["slides"][0]["kind"] == "hero"
    assert manifest["size"] == [2160, 2700]
