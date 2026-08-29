"""eBay source — active Buy-It-Now asking prices.

No API key: scrapes the public search results page
    /sch/i.html?_nkw=lego+<set>&LH_BIN=1                  (active asks)
with Selenium (eBay challenges bare HTTP clients). The parser handles both the
classic `li.s-item` markup and the newer `.s-card` variant eBay A/B tests, and
still understands a sold page when one is supplied from a fixture.

Sold/completed listings used to be fetched here too, but eBay now requires a
signed-in session for them — see `_fetch_html`. eBay is therefore an
asks-only source, and PriceAnalyzer rates it LOW confidence accordingly.

Every listing's title is stored as `description`, so the root PriceAnalyzer
blacklist ('no minifig', 'incomplete', 'box only', …) applies to eBay rows
exactly as it does to BrickLink notes.
"""
import re

from bs4 import BeautifulSoup

from ..models import Listing, ScrapeResult
from .base import BaseScraper, find_prices, polite_sleep, run_cli

SEARCH_URL = "https://www.ebay.com/sch/i.html"

# Listings that are about the set number but are not a saleable set.
JUNK_RE = re.compile(
    r"instructions?\s*only|manual\s*only|box\s*only|sticker|decal|"
    r"minifig(?:ure)?s?\s*only|figures?\s*only|lot\s+of\s+\d|bulk|"
    r"custom|moc\b|compatible|not\s+lego|lepin|repro|poster|"
    r"empty\s+box|parts?\s*only|incomplete",
    re.IGNORECASE,
)
MULTI_QTY_RE = re.compile(r"\b(?:x\s*[2-9]|[2-9]\s*x|two|three)\b.{0,12}sets?\b|\bsets?\b.{0,12}\bx\s*[2-9]\b",
                          re.IGNORECASE)
NEW_RE = re.compile(r"sealed|nisb|nib\b|misb|bnib|new\s+in\s+(?:sealed\s+)?box|"
                    r"brand\s+new|factory\s+sealed|unopened", re.IGNORECASE)
USED_RE = re.compile(r"\bused\b|pre[- ]?owned|built|complete\s+set|100%\s*complete|"
                     r"assembled|displayed|retired\s+built", re.IGNORECASE)
# Prices come from base.find_prices: eBay formats them for the viewer's
# locale, so the same listing reads "$12.34" to one visitor and "ILS 45.60"
# or "45.60 ₪" to another.


class EbaySource(BaseScraper):
    source = "ebay"
    currency = "USD"

    # ── fetch ────────────────────────────────────────────────────────────

    def fixture_paths(self, item_id: str):
        from pathlib import Path
        from ..config import get_config
        base = Path(get_config().fixtures_dir)
        return {"sold": base / f"ebay_{item_id}_sold.html",
                "active": base / f"ebay_{item_id}_active.html"}

    def _build_query(self, item_id: str) -> str:
        return f"lego {item_id}"

    @staticmethod
    def signed_in_profile():
        """Path of the Chrome profile that is signed in to eBay, or None.

        eBay closed the sold/completed search to anonymous clients — every
        variant of `LH_Sold=1&LH_Complete=1` answers with "Error Page",
        "Sign in or Register" or "Security Measure" (five URL shapes checked
        from a clean IP: `python -m brickonomy.doctor <set> --source
        ebay-sold`). With a signed-in profile it works again, so sold prices
        are opt-in: run `python -m brickonomy.ebay_login` once.
        """
        from pathlib import Path

        from ..config import get_config

        raw = get_config().ebay_profile_dir
        if not raw:
            return None
        path = Path(raw).expanduser()
        # Chrome writes "Default/" as soon as a profile is really used; an
        # empty directory means the login was never completed.
        return str(path) if (path / "Default").exists() else None

    def _fetch_html(self, item_id: str, item_type: str = "S"):
        """Sold/completed listings when signed in, plus active Buy-It-Now asks.

        Without a signed-in profile only the active search is fetched — asking
        for sold anyway costs a page load and a polite delay per item and
        returns a block page. PriceAnalyzer then anchors eBay on its
        competitive ask at LOW confidence, exactly as for BrickOwl.
        """
        from .base import make_driver

        nkw = self._build_query(item_id).replace(" ", "+")
        profile = self.signed_in_profile()
        urls = {"active": f"{SEARCH_URL}?_nkw={nkw}&_ipg=120&LH_BIN=1"}
        if profile:
            urls["sold"] = f"{SEARCH_URL}?_nkw={nkw}&_ipg=120&LH_Sold=1&LH_Complete=1"

        htmls = {"sold": "", "active": ""}
        # Headless is what actually blocks a signed-in session: the same
        # cookies that return 65 sold results in a visible window return an
        # interstitial headless. When signed in, use a real (off-screen)
        # window; anonymous asks-only scraping is fine headless.
        driver = make_driver(profile_dir=profile, headless=not profile)
        try:
            if profile:
                # Land on the homepage first. Jumping straight to a sold
                # search with no referrer and no prior session activity is
                # what trips eBay's "Pardon Our Interruption" interstitial.
                driver.get("https://www.ebay.com/")
                polite_sleep()
            for key, url in urls.items():
                driver.get(url)
                polite_sleep()
                htmls[key] = driver.page_source
        finally:
            driver.quit()
        return htmls

    # ── parse ────────────────────────────────────────────────────────────

    def parse(self, htmls, item_id: str) -> ScrapeResult:
        if isinstance(htmls, str):
            htmls = {"sold": htmls, "active": ""}
        res = self.empty_result(item_id)
        res.meta = {"item_id": item_id, "query": self._build_query(item_id)}

        for page_kind, bucket in (("sold", "sold"), ("active", "stock")):
            html = htmls.get(page_kind) or ""
            if not html:
                continue
            for listing, condition in self._parse_cards(html, item_id):
                res.__dict__[condition][bucket].append(listing.as_dict())
        return res

    def _parse_cards(self, html: str, item_id: str):
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("li.s-item") or soup.select(".s-card") or soup.select("li.brwrvr__item-card")
        for card in cards:
            parsed = self._parse_card(card, item_id)
            if parsed:
                yield parsed

    def _parse_card(self, card, item_id: str):
        title_el = (card.select_one(".s-item__title") or card.select_one(".s-card__title")
                    or card.select_one("[role=heading]"))
        price_el = (card.select_one(".s-item__price") or card.select_one(".s-card__price")
                    or card.select_one(".s-item__detail--primary"))
        if not title_el or not price_el:
            return None
        title = title_el.get_text(" ", strip=True)
        price_text = price_el.get_text(" ", strip=True)

        # eBay pads results with a "Shop on eBay" placeholder card.
        if not title or title.lower().startswith("shop on ebay"):
            return None
        # Relevance: the exact set number token must appear.
        if not re.search(rf"\b{re.escape(item_id)}\b", title):
            return None
        if JUNK_RE.search(title) or MULTI_QTY_RE.search(title):
            return None
        # Skip price ranges ("$x to $y") — ambiguous.
        if re.search(r"\bto\b", price_text, re.IGNORECASE) and price_text.count("$") > 1:
            return None
        prices = find_prices(price_text)
        if not prices:
            return None
        price = prices[0][0]

        condition = self._classify_condition(card, title)
        if condition is None:
            return None
        return Listing(qty=1, price=price, status="complete", description=title), condition

    @staticmethod
    def _classify_condition(card, title: str):
        if NEW_RE.search(title):
            return "new"
        if USED_RE.search(title):
            return "used"
        badge = (card.select_one(".SECONDARY_INFO")
                 or card.select_one(".s-item__subtitle")
                 or card.select_one(".s-card__subtitle"))
        if badge:
            text = badge.get_text(" ", strip=True).lower()
            if "brand new" in text or text == "new":
                return "new"
            if "pre-owned" in text or "used" in text or "open box" in text:
                return "used"
        return None  # unclassifiable → drop rather than pollute a bucket


if __name__ == "__main__":
    run_cli(EbaySource())
