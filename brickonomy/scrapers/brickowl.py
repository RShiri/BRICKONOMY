"""BrickOwl source — current asking prices (BrickOwl has no public sold history).

Two-step, plain HTTP (BrickOwl serves server-rendered pages):
  1. resolve the catalog item URL via /search/catalog?query=<set>  (cached in
     items.brickowl_boid by the refresh orchestrator so it runs once per item)
  2. parse the item page's Buy table: new/used prices with quantities.

`sold` stays empty by design — PriceAnalyzer then anchors on the competitive
ask price with confidence LOW, which is the honest signal for asks-only data.
"""
import re

import requests
from bs4 import BeautifulSoup

from ..models import Listing, ScrapeResult
from .base import USER_AGENT, BaseScraper, find_prices, run_cli

BASE_URL = "https://www.brickowl.com"
# Prices come from base.find_prices: BrickOwl renders them in the visitor's
# locale, so they may be bare ("612.00") or symbol-suffixed.


class BrickOwlSource(BaseScraper):
    source = "brickowl"
    currency = "GBP"  # site default; overridden per-scrape from the symbol seen

    def fixture_paths(self, item_id: str):
        from pathlib import Path
        from ..config import get_config
        base = Path(get_config().fixtures_dir)
        return {"item": base / f"brickowl_{item_id}.html"}

    # ── network ──────────────────────────────────────────────────────────

    def resolve_item_url(self, item_id: str, item_type: str = "S"):
        """Search the catalog for the set number, return the item page URL."""
        query = item_id if item_type == "S" else item_id
        resp = self._get(f"{BASE_URL}/search/catalog?query={query}")
        soup = BeautifulSoup(resp, "html.parser")
        # Result links look like /catalog/lego-millennium-falcon-set-75192-1
        for a in soup.find_all("a", href=re.compile(r"^/catalog/")):
            href = a["href"]
            if re.search(rf"\b{re.escape(item_id)}\b", href) or item_id in href:
                return BASE_URL + href
        first = soup.find("a", href=re.compile(r"^/catalog/"))
        if first:
            return BASE_URL + first["href"]
        raise LookupError(f"no BrickOwl catalog result for {item_id!r}")

    def _fetch_html(self, item_id: str, item_type: str = "S"):
        url = self.resolve_item_url(item_id, item_type)
        return self._get(url)

    @staticmethod
    def _get(url):
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        resp.raise_for_status()
        return resp.text

    # ── parse ────────────────────────────────────────────────────────────

    def parse(self, html, item_id: str) -> ScrapeResult:
        if isinstance(html, dict):
            html = html.get("item", "")
        res = self.empty_result(item_id)
        soup = BeautifulSoup(html, "html.parser")
        res.meta = self._parse_meta(soup, item_id)

        page_ccy = self._page_currency(soup)
        seen_ccy = set()
        for cond_word, bucket in (("new", "new"), ("used", "used")):
            for price, qty, ccy, desc in self._find_offers(soup, cond_word, page_ccy):
                seen_ccy.add(ccy)
                res.__dict__[bucket]["stock"].append(
                    Listing(qty=qty, price=price, status="complete",
                            description=desc).as_dict())
        if len(seen_ccy) == 1:
            res.currency = seen_ccy.pop()
        return res

    @staticmethod
    def _page_currency(soup):
        """Currency of the page, from its JSON-LD offer block.

        Needed because prices themselves may render with no symbol at all,
        which leaves nothing to infer the currency from."""
        import json

        for tag in soup.select("script[type='application/ld+json']"):
            try:
                blob = json.loads(tag.string or "{}")
            except (ValueError, TypeError):
                continue
            stack = [blob]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    ccy = node.get("priceCurrency")
                    if isinstance(ccy, str) and len(ccy) == 3:
                        return ccy.upper()
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
        return None

    def _find_offers(self, soup, cond_word: str, page_ccy=None):
        """Yield (price, qty, currency, description) for one condition.

        BrickOwl marks offers with condition labels ('New', 'Used, Complete',
        …) near a price. We scan common offer containers and fall back to
        table rows whose text mentions the condition."""
        containers = soup.select(".buy-table tr, .item-buy tr, table tr")
        for tr in containers:
            text = tr.get_text(" ", strip=True)
            if not text:
                continue
            low = text.lower()
            if cond_word == "new":
                if not re.search(r"\bnew\b", low) or "used" in low:
                    continue
            else:
                if not re.search(r"\bused\b", low):
                    continue
            prices = find_prices(text, default_ccy=page_ccy or "GBP")
            if not prices:
                continue
            price, ccy = prices[0]
            qty = 1
            qm = re.search(r"(\d+)\s+(?:available|in stock)", low)
            if qm:
                qty = max(1, min(int(qm.group(1)), 50))
            yield price, qty, ccy, text[:140]

    @staticmethod
    def _parse_meta(soup, item_id: str):
        meta = {"item_id": item_id}
        title = soup.find("h1")
        if title:
            meta["item_name"] = re.sub(r"\s*LEGO\s*", "", title.get_text(strip=True), count=1).strip()
        text = soup.get_text(" ", strip=True)
        pieces = re.search(r"([\d,]+)\s+Pieces", text)
        if pieces:
            meta.setdefault("specs", {})["parts"] = int(pieces.group(1).replace(",", ""))
        minifigs = re.search(r"(\d+)\s+Minifig", text)
        if minifigs:
            meta.setdefault("specs", {})["minifigs"] = int(minifigs.group(1))
        year = re.search(r"\b(19|20)(\d{2})\b\s*(?:Retired|Released)|Released\D{0,10}\b((?:19|20)\d{2})\b", text)
        if year:
            meta["year_released"] = int(year.group(3) or (year.group(1) + year.group(2)))
        return meta


if __name__ == "__main__":
    run_cli(BrickOwlSource())
