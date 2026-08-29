"""Shared scraper machinery.

Every source implements:
    parse(html, item_id)  — pure HTML→ScrapeResult, unit-testable on fixtures
    fetch(item_id, ...)   — network wrapper that never raises: on any failure
                            it returns an empty ScrapeResult with .error set

Fixture mode (config.fixture_mode or --fixture) makes fetch() read saved HTML
from tests/fixtures/ instead of the network, so everything runs offline.
"""
import argparse
import json
import random
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import get_config
from ..models import ScrapeResult

# Used for the plain-`requests` fetches (BrickLink POV, BrickOwl). Selenium
# sessions keep the browser's own UA — see make_driver.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


# ── prices ───────────────────────────────────────────────────────────────
#
# One definition, shared by every scraper AND by the doctor. Keeping separate
# copies is what hid a real bug for days: BrickOwl renders prices in the
# visitor's locale, which for some regions means a bare "612.00", so a
# symbol-required regex parsed nothing from a page holding 15 offers — while
# the doctor, using its own equally strict copy, cheerfully confirmed there
# were "no prices on the page". A diagnostic that can disagree with the
# parser is worse than no diagnostic.
#
# Accepts: "$12.34", "US $12.34", "ILS 45.60", "45.60 ₪", "45.60".
SYMBOL_TO_CCY = {"£": "GBP", "$": "USD", "€": "EUR", "₪": "ILS"}
CODE_TO_CCY = {"US": "USD", "CA": "CAD", "AU": "AUD", "NZ": "NZD"}

PRICE_RE = re.compile(
    r"(?:(?P<code>[A-Z]{2,3})\s*)?(?P<sym>[£$€₪])?\s*"
    r"(?P<amt>\d[\d,]*\.\d{1,2})\s*(?P<sym2>[£$€₪])?")


def price_currency(match, default=None):
    """Currency of one PRICE_RE match: symbol either side, then ISO code."""
    symbol = match.group("sym") or match.group("sym2")
    return (SYMBOL_TO_CCY.get(symbol)
            or CODE_TO_CCY.get((match.group("code") or "").upper())
            or default)


def find_prices(text, default_ccy=None):
    """[(amount, currency|default), …] for every price in `text`.

    Requires decimals on purpose: bare integers in marketplace copy are years,
    set numbers and piece counts far more often than they are prices.
    """
    out = []
    for m in PRICE_RE.finditer(text or ""):
        try:
            amount = float(m.group("amt").replace(",", ""))
        except ValueError:
            continue
        if amount > 0:
            out.append((amount, price_currency(m, default_ccy)))
    return out


def polite_sleep():
    lo, hi = get_config().request_delay_range
    time.sleep(random.uniform(lo, hi))


def make_driver(profile_dir=None, headless=True):
    """Headless Chrome mirroring the root scraper's setup, plus stealth flags.

    profile_dir: reuse a persistent Chrome profile. Signing in once inside
    that profile (see `brickonomy.ebay_login`) makes the session's cookies
    available to later scrapes, which is what eBay's sold/completed search
    now requires. Nothing is stored by Brickonomy itself — the cookies live
    in Chrome's own profile directory.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if profile_dir:
        opts.add_argument(f"--user-data-dir={profile_dir}")
    if headless:
        opts.add_argument("--headless=new")
    else:
        # A real window, parked off-screen. Some sites (eBay) reject headless
        # Chrome outright even with valid cookies, so scraping them needs a
        # genuine window — but nobody wants it popping up once per set.
        opts.add_argument("--window-position=-2400,-2400")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--log-level=3")
    # Deliberately NOT overriding the user agent. Chrome sends its real
    # version in sec-ch-ua client hints no matter what the UA string says, so
    # pinning a stale "Chrome/119" string only creates a mismatch that bot
    # detection reads as automation. The browser's own UA is consistent.
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1440,900")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(40)

    # navigator.webdriver is true for every Selenium session and is the first
    # thing anti-bot scripts read. Patch it before any page script runs.
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', "
                      "{get: () => undefined});"
        })
    except Exception:
        pass          # non-Chromium driver, or CDP unavailable — not fatal
    return driver


class BaseScraper(ABC):
    source: str = ""
    currency: str = ""

    def empty_result(self, item_id, error=None) -> ScrapeResult:
        return ScrapeResult(item_id=item_id, source=self.source,
                            currency=self.currency, error=error)

    @abstractmethod
    def parse(self, html, item_id: str) -> ScrapeResult:
        """html: str, or dict of named pages for multi-page sources."""

    @abstractmethod
    def _fetch_html(self, item_id: str, item_type: str = "S"):
        """Network fetch returning whatever parse() expects. May raise."""

    # ── fixtures ─────────────────────────────────────────────────────────

    def fixture_paths(self, item_id: str):
        base = Path(get_config().fixtures_dir)
        return {"default": base / f"{self.source}_{item_id}.html"}

    def _load_fixture(self, item_id: str):
        paths = self.fixture_paths(item_id)
        if len(paths) == 1:
            path = next(iter(paths.values()))
            if not path.exists():
                raise FileNotFoundError(f"fixture missing: {path}")
            return path.read_text(encoding="utf-8", errors="replace")
        htmls = {}
        for key, path in paths.items():
            htmls[key] = (path.read_text(encoding="utf-8", errors="replace")
                          if path.exists() else "")
        if not any(htmls.values()):
            raise FileNotFoundError(f"no fixtures found under {get_config().fixtures_dir}")
        return htmls

    def _save_fixture(self, item_id: str, html):
        paths = self.fixture_paths(item_id)
        base = Path(get_config().fixtures_dir)
        base.mkdir(parents=True, exist_ok=True)
        if isinstance(html, dict):
            for key, content in html.items():
                if content:
                    paths[key].write_text(content, encoding="utf-8")
        else:
            next(iter(paths.values())).write_text(html, encoding="utf-8")

    # ── public entry point ──────────────────────────────────────────────

    def fetch(self, item_id: str, item_type: str = "S",
              fixture: bool = None, save_fixture: bool = False) -> ScrapeResult:
        use_fixture = get_config().fixture_mode if fixture is None else fixture
        try:
            if use_fixture:
                html = self._load_fixture(item_id)
            else:
                html = self._fetch_html(item_id, item_type)
                if save_fixture:
                    self._save_fixture(item_id, html)
            return self.parse(html, item_id)
        except Exception as exc:  # graceful degradation by contract
            return self.empty_result(item_id, error=f"{type(exc).__name__}: {exc}")


def run_cli(scraper: BaseScraper):
    """Shared __main__ for `python -m brickonomy.scrapers.<source> <item_id>`."""
    ap = argparse.ArgumentParser(description=f"{scraper.source} scraper")
    ap.add_argument("item_id")
    ap.add_argument("--type", default="S", choices=["S", "M"])
    ap.add_argument("--fixture", action="store_true", help="parse saved fixture instead of the network")
    ap.add_argument("--save-fixture", action="store_true", help="save fetched HTML into tests/fixtures/")
    args = ap.parse_args()

    res = scraper.fetch(args.item_id, args.type,
                        fixture=args.fixture or None, save_fixture=args.save_fixture)
    out = {
        "item_id": res.item_id, "source": res.source, "currency": res.currency,
        "error": res.error, "meta": res.meta,
        "counts": {c: {k: len(res.__dict__[c][k]) for k in ("sold", "stock")}
                   for c in ("new", "used")},
    }
    print(json.dumps(out, indent=2))

    if not res.is_empty:
        from ..compat import PriceAnalyzer
        analysis = PriceAnalyzer(res.analyzer_input()).analyze()
        for cond in ("new", "used"):
            c = analysis[cond]
            print(f"{cond:>4}: market {c['market_price']:.2f} {res.currency} "
                  f"({c['confidence']}, sold n={c['stats']['sold']['final_count']}, "
                  f"stock n={c['stats']['stock']['final_count']})")
    raise SystemExit(2 if res.error else 0)
