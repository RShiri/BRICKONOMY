"""Diagnose a source that returns nothing.

  python -m brickonomy.doctor 75192            # all sources
  python -m brickonomy.doctor 75192 --source ebay
  python -m brickonomy.doctor 75192 --dump     # also save the HTML next to it

When eBay or BrickOwl report "no listings found", the cause is one of: the
request was blocked (challenge page / 403), the page shape changed, or the
item genuinely has no offers. This prints enough of what came back to tell
those apart — HTTP status, page size and title, whether the response smells
like a block, how many candidate rows each selector matches, and the most
common CSS classes on the page so a changed layout is obvious.

Everything it prints comes from the live fetch the scraper itself performs, so
a clean run here means the scraper's own fetch works too.
"""
import argparse
import re
from collections import Counter

SEP = "─" * 68


def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def _describe(html, label):
    """Size, title and block-detection for one fetched page."""
    if not html:
        print(f"  {label}: EMPTY response")
        return None
    soup = _soup(html)
    title = (soup.title.get_text(strip=True) if soup.title else "")[:80]
    print(f"  {label}: {len(html):,} bytes · title={title!r}")
    # Only trust strong signals: "cloudflare" and "robot" appear in ordinary
    # footers, so matching them anywhere produced false alarms.
    low = html[:6000].lower()
    signals = [w for w in ("captcha", "are you a human", "access denied",
                           "unusual traffic", "pardon our interruption",
                           "checking your browser", "verify you are human")
               if w in low]
    if "error page" in title.lower() or "denied" in title.lower():
        signals.append(f"title says {title!r}")
    if signals:
        print(f"    ⚠ looks blocked — {', '.join(signals)}")
    return soup


def _selector_counts(soup, selectors, label="selectors"):
    print(f"    {label}:")
    for sel in selectors:
        try:
            n = len(soup.select(sel))
        except Exception as exc:                       # bad selector, not fatal
            n = f"error: {exc}"
        print(f"      {sel:<34} {n}")


def _common_classes(soup, n=14):
    """Most frequent class tokens — a changed layout shows up here first."""
    counter = Counter()
    for el in soup.find_all(class_=True):
        for cls in el.get("class", []):
            counter[cls] += 1
    top = ", ".join(f"{c}×{k}" for c, k in counter.most_common(n))
    print(f"    most common classes: {top}")


def _prices_seen(soup, n=6, context=False):
    # Deliberately the scrapers' own detector. A private copy here once
    # reported "NONE" on a page the parser read 12 offers from, which sent
    # the investigation down three wrong paths.
    from .scrapers.base import PRICE_RE

    text = soup.get_text(" ", strip=True)
    pattern = PRICE_RE
    found = [m.group(0).strip() for m in pattern.finditer(text)][:n]
    print(f"    price-looking strings on the page: {found or 'NONE'}")
    if context:
        # The words around a price are what a parser has to key off, so print
        # them verbatim when a regex is being rewritten.
        for m in list(pattern.finditer(text))[:n]:
            start, end = max(0, m.start() - 110), min(len(text), m.end() + 40)
            print(f"      … {text[start:end]} …")


def _dump(html, path):
    from pathlib import Path
    Path(path).write_text(html, encoding="utf-8", errors="replace")
    print(f"    saved → {path}")


# ── per source ──────────────────────────────────────────────────────────

def check_brickowl(item_id, dump=False):
    from .scrapers.brickowl import BrickOwlSource

    print(f"\n{SEP}\nBRICKOWL — plain HTTP\n{SEP}")
    src = BrickOwlSource()

    try:
        search_html = src._get(f"https://www.brickowl.com/search/catalog?query={item_id}")
    except Exception as exc:
        print(f"  ✘ search request failed: {type(exc).__name__}: {exc}")
        return
    soup = _describe(search_html, "search page")
    if soup is None:
        return
    links = [a["href"] for a in soup.find_all("a", href=re.compile(r"^/catalog/"))][:8]
    print(f"    /catalog/ links found: {len(links)}")
    for href in links[:5]:
        print(f"      {href}")
    if dump:
        _dump(search_html, f"doctor_brickowl_{item_id}_search.html")

    try:
        url = src.resolve_item_url(item_id)
        print(f"  resolved item URL: {url}")
        item_html = src._get(url)
    except Exception as exc:
        print(f"  ✘ could not resolve/fetch the item page: {type(exc).__name__}: {exc}")
        return
    isoup = _describe(item_html, "item page")
    if isoup is None:
        return
    _selector_counts(isoup, [".buy-table tr", ".item-buy tr", "table tr",
                             "[class*=buy]", "[class*=offer]", "[class*=price]",
                             "[data-price]", "script[type='application/ld+json']"])
    _common_classes(isoup)
    _prices_seen(isoup)
    if dump:
        _dump(item_html, f"doctor_brickowl_{item_id}_item.html")

    res = src.parse({"item": item_html}, item_id)
    print(f"  → parser produced: new stock={len(res.new['stock'])} "
          f"used stock={len(res.used['stock'])} currency={res.currency}")


def check_ebay(item_id, dump=False):
    from .scrapers.ebay import EbaySource

    print(f"\n{SEP}\nEBAY — headless Chrome\n{SEP}")
    src = EbaySource()
    try:
        htmls = src._fetch_html(item_id)
    except Exception as exc:
        print(f"  ✘ fetch failed: {type(exc).__name__}: {exc}")
        return

    for key in ("sold", "active"):
        html = htmls.get(key, "")
        soup = _describe(html, f"{key} page")
        if soup is None:
            continue
        _selector_counts(soup, ["li.s-item", ".s-item", ".s-card",
                                ".s-item__price", ".s-card__price",
                                "[class*=s-item]", "[class*=s-card]",
                                "ul.srp-results li", "[data-testid]"])
        _common_classes(soup)
        _prices_seen(soup)
        if dump:
            _dump(html, f"doctor_ebay_{item_id}_{key}.html")

    res = src.parse(htmls, item_id)
    print(f"  → parser produced: new sold={len(res.new['sold'])} "
          f"new stock={len(res.new['stock'])} used sold={len(res.used['sold'])} "
          f"used stock={len(res.used['stock'])}")


def check_ebay_sold_variants(item_id, dump=False):
    """eBay's sold/completed search is the fragile one — it answers with an
    error page for some parameter combinations while the identical active
    search works. Try the plausible variants and report which return cards."""
    from .scrapers.base import make_driver, polite_sleep
    from .scrapers.ebay import SEARCH_URL

    print(f"\n{SEP}\nEBAY — sold-search URL variants\n{SEP}")
    nkw = f"lego+{item_id}"
    variants = {
        "current (ipg=120)": f"{SEARCH_URL}?_nkw={nkw}&_ipg=120&LH_Sold=1&LH_Complete=1",
        "no _ipg": f"{SEARCH_URL}?_nkw={nkw}&LH_Sold=1&LH_Complete=1",
        "sacat + no ipg": f"{SEARCH_URL}?_nkw={nkw}&_sacat=0&LH_Sold=1&LH_Complete=1",
        "sold only": f"{SEARCH_URL}?_nkw={nkw}&LH_Sold=1",
        "ipg=60": f"{SEARCH_URL}?_nkw={nkw}&_ipg=60&LH_Sold=1&LH_Complete=1",
    }
    driver = make_driver()
    try:
        for label, url in variants.items():
            try:
                driver.get(url)
                polite_sleep()
                html = driver.page_source
            except Exception as exc:
                print(f"  {label}: ✘ {type(exc).__name__}: {exc}")
                continue
            soup = _soup(html)
            title = (soup.title.get_text(strip=True) if soup.title else "")[:40]
            cards = len(soup.select(".s-card, li.s-item"))
            prices = len(soup.select(".s-card__price, .s-item__price"))
            verdict = "OK" if cards else "no results"
            print(f"  {label:<20} {len(html):>9,}b  cards={cards:<4} prices={prices:<4} "
                  f"{verdict}  title={title!r}")
            if dump and cards:
                _dump(html, f"doctor_ebay_{item_id}_sold_{label.split()[0]}.html")
    finally:
        driver.quit()


def check_bricklink_pov(item_id, dump=False):
    from .scrapers.bricklink import POV_URL, BrickLinkSource

    print(f"\n{SEP}\nBRICKLINK — part-out value page\n{SEP}")
    src = BrickLinkSource()
    num = item_id.split("-")[0]
    url = (f"{POV_URL}?itemType=S&itemNo={num}&itemSeq=1&itemQty=1"
           f"&breakType=M&itemCondition=N")
    print(f"  {url}")
    html, error = src._get(url)
    if error:
        print(f"  ✘ fetch error: {error}")
        return
    soup = _describe(html, "POV page")
    if soup is None:
        return
    text = soup.get_text(" ", strip=True)
    for label in ("Average Value", "Total", "Qty Avg", "Sold", "no inventory",
                  "not available", "Sorry"):
        idx = text.lower().find(label.lower())
        if idx >= 0:
            print(f"    …{text[max(0, idx - 60):idx + 90]}…")
    _prices_seen(soup, n=8, context=True)
    print(f"    page text (first 900 chars):\n      {text[:900]}")
    if dump:
        _dump(html, f"doctor_bricklink_{item_id}_pov.html")
    print(f"  → parser produced: {src.parse_part_out_value(html)}")


def main():
    ap = argparse.ArgumentParser(description="Diagnose why a source returns nothing")
    ap.add_argument("item_id", nargs="?", default="75192")
    ap.add_argument("--source",
                    choices=["ebay", "brickowl", "pov", "ebay-sold", "all"],
                    default="all")
    ap.add_argument("--dump", action="store_true",
                    help="write the fetched HTML to files in the current directory")
    args = ap.parse_args()

    print(f"Brickonomy doctor — item {args.item_id}")
    checks = {"brickowl": check_brickowl, "ebay": check_ebay,
              "ebay-sold": check_ebay_sold_variants, "pov": check_bricklink_pov}
    for name, fn in checks.items():
        if args.source in (name, "all"):
            try:
                fn(args.item_id, dump=args.dump)
            except Exception as exc:
                print(f"  ✘ {name} check crashed: {type(exc).__name__}: {exc}")
    print(f"\n{SEP}\nDone.")


if __name__ == "__main__":
    main()
