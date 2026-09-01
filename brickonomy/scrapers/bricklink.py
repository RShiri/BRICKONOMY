"""BrickLink source.

Price guide: delegates to the root BrickLinkScraper (same Selenium flow and
HTML parser the repo already uses; prices arrive in ILS).

Additions over the root scraper:
  - fetch_parts_inventory(): the exact parts list from the set page
    (catalogItemInv.asp — the page root code already scrapes for minifigs,
    here with viewItemType=P).
  - fetch_part_out_value(): the set's part-out total from catalogPOV.asp,
    one request per set instead of per-part price lookups.
  - fetch_offers(): the current listings from the site's own Items For Sale
    endpoint, which unlike the price guide says whether each lot is the whole
    item or a piece of it.
"""
import json
import re

import requests
from bs4 import BeautifulSoup

from ..compat import get_bricklink_scraper
from ..models import ScrapeResult
from .base import USER_AGENT, BaseScraper, run_cli

BASE_URL = "https://www.bricklink.com/v2/catalog/catalogitem.page"

INV_URL = "https://www.bricklink.com/catalogItemInv.asp"
POV_URL = "https://www.bricklink.com/catalogPOV.asp"
TREE_URL = "https://www.bricklink.com/catalogTree.asp"
LIST_URL = "https://www.bricklink.com/catalogList.asp"


# Header and footer text that sits in the same table as the part rows and
# would otherwise be stored as a part.
_NOT_A_PART = {"Item No", "Item  No", "Qty", "Description", "Image", "Color"}


class BrickLinkSource(BaseScraper):
    source = "bricklink"
    currency = "ILS"          # default; the real one is detected per scrape
    BASE_URL = BASE_URL

    def parse(self, html, item_id: str) -> ScrapeResult:
        scraper_cls = get_bricklink_scraper()
        data = scraper_cls._parse_html(scraper_cls.__new__(scraper_cls), item_id, html)
        res = self.empty_result(item_id)
        res.meta = data.get("meta", {})
        res.new = data.get("new", res.new)
        res.used = data.get("used", res.used)
        res.currency = self._dominant_currency(res)
        return res

    @staticmethod
    def _dominant_currency(res: ScrapeResult) -> str:
        """BrickLink renders prices in the session's currency, so all rows of a
        scrape share one. Take the most common tag the parser detected."""
        counts = {}
        for condition in ("new", "used"):
            for kind in ("sold", "stock"):
                for row in res.__dict__[condition][kind]:
                    ccy = row.get("currency")
                    if ccy:
                        counts[ccy] = counts.get(ccy, 0) + 1
        return max(counts, key=counts.get) if counts else BrickLinkSource.currency

    def _fetch_html(self, item_id: str, item_type: str = "S"):
        """Playwright engine: fetch the price-guide page's HTML.

        Selenium goes through the root scraper instead (see fetch()), which
        already owns the driver lifecycle and its own caching.
        """
        from playwright.sync_api import sync_playwright

        url = f"{self.BASE_URL}?{item_type}={item_id}#T=P"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                # Warm the session first: BrickLink redirects catalog deep
                # links when it has never seen the client before.
                page.goto("https://www.bricklink.com", wait_until="domcontentloaded",
                          timeout=30000)
                page.goto(url, wait_until="networkidle", timeout=40000)
                page.wait_for_selector(".pcipgInnerTable", timeout=20000)
                # The tables render as placeholders first; wait for real prices.
                page.wait_for_function(
                    "() => /[$₪€£]|~/.test(document.body.innerText)", timeout=15000
                )
                return page.content()
            finally:
                browser.close()

    def fetch(self, item_id: str, item_type: str = "S",
              fixture: bool = None, save_fixture: bool = False) -> ScrapeResult:
        from ..config import get_config

        cfg = get_config()
        use_fixture = cfg.fixture_mode if fixture is None else fixture
        if use_fixture:
            try:
                return self.parse(self._load_fixture(item_id), item_id)
            except Exception as exc:
                return self.empty_result(item_id, error=f"{type(exc).__name__}: {exc}")

        if getattr(cfg, "scrape_engine", "selenium") == "playwright":
            try:
                html = self._fetch_html(item_id, item_type)
                if save_fixture:
                    self._save_fixture(item_id, html)
                return self.parse(html, item_id)
            except ImportError:
                pass  # playwright not installed — fall back to Selenium
            except Exception as exc:
                return self.empty_result(item_id, error=f"{type(exc).__name__}: {exc}")

        try:
            scraper = get_bricklink_scraper()()
            data = scraper.scrape(item_id, item_type, force=True)
            res = self.empty_result(item_id)
            res.meta = data.get("meta", {})
            res.new = data.get("new", res.new)
            res.used = data.get("used", res.used)
            res.currency = self._dominant_currency(res)
            return res
        except Exception as exc:
            return self.empty_result(item_id, error=f"{type(exc).__name__}: {exc}")

    # ── parts inventory ──────────────────────────────────────────────────

    # Common BrickLink colour names, longest-first, for splitting the
    # "ColorName PartName" description text the inventory page renders.
    # Longest first, so "Dark Bluish Gray" is matched before "Dark" and
    # "Trans-Light Blue" before "Trans-Light". A description whose colour is
    # not listed keeps its full text as the part name rather than losing it.
    _COLOR_NAMES = sorted([
        "Light Bluish Gray", "Dark Bluish Gray", "Reddish Brown", "Dark Red",
        "Dark Blue", "Dark Green", "Dark Tan", "Light Gray", "Dark Gray",
        "Sand Blue", "Sand Green", "Sand Red", "Sand Purple",
        "Medium Blue", "Medium Azure", "Dark Azure", "Medium Lavender",
        "Bright Light Orange", "Bright Light Yellow", "Bright Light Blue",
        "Bright Green", "Bright Pink", "Dark Pink", "Light Pink",
        "Olive Green", "Dark Orange", "Medium Orange", "Neon Orange",
        "Dark Brown", "Medium Nougat", "Light Nougat", "Dark Nougat",
        "Dark Purple", "Medium Green", "Light Aqua", "Dark Turquoise",
        "Very Light Bluish Gray", "Very Light Gray", "Light Yellow",
        "Pearl Gold", "Pearl Dark Gray", "Pearl Light Gray", "Pearl White",
        "Flat Silver", "Metallic Silver", "Metallic Gold", "Chrome Silver",
        "Chrome Gold", "Satin Trans-Clear", "Glow In Dark Opaque",
        "Trans-Clear", "Trans-Red", "Trans-Light Blue", "Trans-Orange",
        "Trans-Neon Green", "Trans-Neon Orange", "Trans-Dark Blue",
        "Trans-Black", "Trans-Purple", "Trans-Yellow", "Trans-Green",
        "Trans-Bright Green", "Trans-Medium Blue", "Trans-Pink",
        "Trans-Brown", "Trans-Dark Pink", "Trans-Light Green",
        "Nougat", "Aqua", "Salmon", "Sand Yellow", "Dark Yellow",
        "White", "Black", "Red", "Blue", "Yellow", "Green", "Tan", "Orange",
        "Brown", "Lime", "Purple", "Magenta", "Coral", "Lavender", "Azure",
        "Pink", "Gold", "Silver", "Copper",
    ], key=len, reverse=True)

    def parse_parts_inventory(self, html: str):
        """Rows: {part_no, part_name, color_id, color_name, qty}."""
        soup = BeautifulSoup(html, "html.parser")
        parts = []
        by_lot = {}
        for tr in soup.find_all("tr"):
            links = tr.find_all("a", href=re.compile(r"\?P="))
            if not links:
                continue
            m = re.search(r"\?P=([^&\"]+)", links[0].get("href", ""))
            if not m:
                continue
            part_no = m.group(1)

            tds = tr.find_all("td")
            qty = None
            for td in tds:
                txt = td.get_text(strip=True)
                if txt.isdigit():
                    qty = int(txt)
                    break
            if qty is None:
                continue

            # The description is the <b> in the fourth cell — "Black Arch 1 x
            # 5 x 4 - Continuous Bow", with the Catalog:Parts:Arch breadcrumb
            # in a sibling <font> that must not come along. The old heuristic
            # took the longest ?P= link text in the row, but a row has only
            # two such links: the image, whose text is empty, and the item
            # number, whose text is the number. So every part in the database
            # was stored with its own part number as its name, and the colour
            # split below never matched, leaving colour empty on all 4,100
            # rows. This is the same mistake the minifig parser made.
            desc = ""
            if len(tds) > 3:
                bold = tds[3].find("b")
                if bold:
                    desc = bold.get_text(" ", strip=True)
            # No <b> description means this is not a part row. The table's own
            # header matches everything above it — it has cells, a digit, and
            # sits under the same links — and used to be stored as a part
            # named "Item No".
            desc = desc.replace(" ", " ").strip()
            if not desc or desc == part_no or desc in _NOT_A_PART:
                continue
            color_name, part_name = None, desc
            for cname in self._COLOR_NAMES:
                if desc.startswith(cname + " "):
                    color_name = cname
                    part_name = desc[len(cname):].strip()
                    break

            color_id = 0
            cid = re.search(r"(?:idColor|colorID)=(\d+)", str(tr))
            if cid:
                color_id = int(cid.group(1))

            # One lot per part and colour. The page repeats a row inside its
            # own wrapper, which produced three rows for 76051's sticker
            # sheet; keep the one carrying the fullest description.
            key = (part_no, color_id)
            existing = by_lot.get(key)
            if existing is None:
                by_lot[key] = {
                    "part_no": part_no, "part_name": part_name,
                    "color_id": color_id, "color_name": color_name,
                    "qty": qty,
                }
            elif len(part_name) > len(existing["part_name"]):
                existing.update(part_name=part_name, color_name=color_name)
        return list(by_lot.values())

    def fetch_parts_inventory(self, set_id: str):
        """Returns (parts, error). Uses plain HTTP first (server-rendered page),
        Selenium as fallback."""
        url = f"{INV_URL}?S={set_id if '-' in set_id else set_id + '-1'}&viewItemType=P"
        html, error = self._get(url)
        if html:
            parts = self.parse_parts_inventory(html)
            if parts:
                return parts, None
            error = "no parts parsed from inventory page"
        return [], error

    # ── part-out value ───────────────────────────────────────────────────

    # The page renders one line per basis, e.g.
    #   "* Average of last 6 months Sales: US $1,234.66 Including 7521 Items in 684 Lots."
    #   "Current Items For Sale Average: US $1,620.09 Including …"
    # Sold prices first — that is what the parts actually fetch; the asking
    # average is the fallback for a set with no recent sales.
    POV_PATTERNS = (
        r"Average\s+of\s+last\s+\d+\s+months?\s+Sales\s*:",
        r"Current\s+Items?\s+For\s+Sale\s+Average\s*:",
        r"Average\s+Value[^:]{0,40}:",          # older layout
    )
    # "US $1,234.66", "ILS ₪1,234.66", "£1,234.66"
    POV_AMOUNT = r"\s*(?:(US|CA|AU|NZ)\s*)?(?:([A-Z]{2,3})\s*)?([$₪€£])?\s*([\d,]+\.\d{2})"
    CCY_BY_SYMBOL = {"$": "USD", "₪": "ILS", "€": "EUR", "£": "GBP"}

    def parse_part_out_value(self, html: str):
        """Part-out total from catalogPOV.asp.

        Returns (value, currency) — the page is fetched without a BrickLink
        session, so it comes back in the site default (USD) rather than the
        scraper's session currency, and the caller has to convert."""
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        for pattern in self.POV_PATTERNS:
            m = re.search(pattern + self.POV_AMOUNT, text, re.IGNORECASE)
            if not m:
                continue
            value = float(m.group(4).replace(",", ""))
            if value <= 0:
                continue
            ccy = (m.group(2) if m.group(2) and len(m.group(2)) == 3 else None)
            if not ccy:
                ccy = "USD" if m.group(1) else self.CCY_BY_SYMBOL.get(m.group(3), "USD")
            return value, ccy
        return None, None

    def fetch_part_out_value(self, set_id: str, condition: str = "N"):
        """Returns (value, currency, error)."""
        num = set_id.split("-")[0]
        url = (f"{POV_URL}?itemType=S&itemNo={num}&itemSeq=1&itemQty=1"
               f"&breakType=M&itemCondition={condition}")
        html, error = self._get(url)
        if html:
            value, ccy = self.parse_part_out_value(html)
            if value is not None:
                return value, ccy, None
            error = "POV total not found on page"
        return None, None, error

    # ── catalog tree (themes / subthemes) ────────────────────────────────

    def parse_category_tree(self, html: str):
        """Parse catalogTree.asp into ordered rows
        [{cat_id, name, depth}, ...]; depth comes from the indentation
        BrickLink renders before each category link."""
        soup = BeautifulSoup(html, "html.parser")
        cats, seen = [], set()
        for a in soup.find_all("a", href=re.compile(r"catalogList\.asp\?[^\"]*catID=\d+")):
            m = re.search(r"catID=(\d+)", a["href"])
            if not m:
                continue
            cat_id = int(m.group(1))
            name = a.get_text(" ", strip=True)
            if not name or cat_id in seen:
                continue
            seen.add(cat_id)

            # Indentation: BrickLink pads nested categories with &nbsp; runs
            # (or nested lists) before the link inside the same cell/row.
            depth = 0
            cell = a.find_parent(["td", "div", "li"])
            if cell is not None:
                if cell.name == "li":
                    depth = max(0, len(cell.find_parents("ul")) - 1)
                else:
                    prefix = []
                    for node in cell.descendants:
                        if node is a:
                            break
                        if isinstance(node, str):
                            prefix.append(node)
                    nbsp = "".join(prefix).count("\xa0")
                    depth = nbsp // 3
            cats.append({"cat_id": cat_id, "name": name, "depth": max(0, depth)})
        return cats

    def fetch_category_tree(self, item_type: str = "S"):
        html, error = self._get(f"{TREE_URL}?itemType={item_type}")
        if html:
            cats = self.parse_category_tree(html)
            if cats:
                return cats, None
            error = "no categories parsed from tree page"
        return [], error

    # ── catalog list (all sets in a category) ────────────────────────────

    def parse_catalog_list(self, html: str):
        """Parse one catalogList.asp page.

        Returns (sets, total_pages) with sets =
        [{item_id, name, year}, ...] (item_id without the -1 suffix)."""
        soup = BeautifulSoup(html, "html.parser")
        sets, seen = [], set()
        for a in soup.find_all("a", href=re.compile(r"catalogitem\.page\?S=")):
            m = re.search(r"\?S=([\w.-]+)", a["href"])
            if not m:
                continue
            raw = m.group(1)
            item_id = raw.split("-")[0]
            text = a.get_text(" ", strip=True)
            if item_id in seen:
                # second link for the same set usually carries the name
                if text and text != raw:
                    for s in sets:
                        if s["item_id"] == item_id and not s["name"]:
                            s["name"] = text
                continue
            seen.add(item_id)
            entry = {"item_id": item_id, "name": text if text != raw else "", "year": None}

            row = a.find_parent("tr") or a.parent
            if row:
                ym = re.search(r"itemYear=(\d{4})", str(row))
                if not ym:
                    ym = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", row.get_text(" ", strip=True))
                if ym:
                    entry["year"] = int(ym.group(1))
            sets.append(entry)

        total_pages = 1
        for pg in soup.find_all("a", href=re.compile(r"pg=(\d+)")):
            m = re.search(r"pg=(\d+)", pg["href"])
            if m:
                total_pages = max(total_pages, int(m.group(1)))
        m = re.search(r"Page\s+\d+\s+of\s+(\d+)", soup.get_text(" ", strip=True))
        if m:
            total_pages = max(total_pages, int(m.group(1)))
        return sets, total_pages

    def fetch_catalog_page(self, cat_id: int, page: int = 1, item_type: str = "S"):
        url = (f"{LIST_URL}?catType={item_type}&catID={cat_id}"
               f"&v=0&pg={page}&sortBy=Y&sortAsc=D")
        html, error = self._get(url)
        if html:
            return self.parse_catalog_list(html) + (None,)
        return [], 0, error

    # ── minifig inventory of a set ───────────────────────────────────────


    # ── current offers ───────────────────────────────────────────────────
    # BrickLink's price guide, which is what fetch() reads, reports the asks
    # for an item without saying whether each one is for the whole thing. For
    # set 76049 that meant the cheapest "listing" was ILS 11.88 for Captain
    # America's backpack, and the buy signal quoted it as 53% under market.
    # The cheapest listing that is actually the set is ILS 327.65.
    #
    # This endpoint is what the site's own Items For Sale tab calls, and it
    # answers in JSON with the fields the price guide never had: whether the
    # lot is complete, the seller's own description, and their country. It is
    # keyed on BrickLink's internal item id rather than the set number.
    IFS_URL = "https://www.bricklink.com/ajax/clone/catalogifs.ajax"

    # codeComplete: B is a part of the item, C the whole thing, S still sealed.
    _INCOMPLETE_CODE = "B"


    # The offers endpoint is an XHR the catalog page makes, and it is treated
    # as one: called cold it answers 403, and only a client carrying the
    # cookies that page sets — and naming it as the referer — gets JSON. The
    # first attempt appeared to work only because _get fell back to Selenium,
    # which had them; the moment plain HTTP was used it was refused, and 40
    # sets in a row recorded "no offers" for what was really a closed door.
    #
    # One session per process, warmed once. After that an item costs a single
    # request, which is both faster and politer than the two it replaced.
    _ifs_session = None

    @classmethod
    def _offers_session(cls):
        import requests

        if cls._ifs_session is not None:
            return cls._ifs_session
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        try:
            session.get(f"{BASE_URL}?S=75192-1", timeout=25)
        except Exception:
            pass                      # an unwarmed session still gets a try
        cls._ifs_session = session
        return session

    @classmethod
    def _get_offers_json(cls, internal_id, item_id, item_type, per_page):
        session = cls._offers_session()
        referer = f"{BASE_URL}?{item_type}={item_id}"
        try:
            resp = session.get(
                f"{cls.IFS_URL}?itemid={internal_id}&rpp={per_page}&pi=1&ci=0",
                headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
                timeout=25)
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if resp.status_code == 403:
            # The session has gone stale; drop it so the next call rebuilds one
            # rather than every remaining item inheriting the refusal.
            cls._ifs_session = None
            return None, "refused (403) — session expired"
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        if not resp.text.lstrip().startswith("{"):
            return None, "not JSON (blocked or challenged)"
        return resp.text, None

    @staticmethod
    def find_internal_id(item_id: str, item_type: str = "S"):
        """BrickLink's numeric id for an item, or None.

        Costs a page fetch, so callers cache it on the item — the id never
        changes, and the offers endpoint needs it on every call.
        """
        suffix = item_id if "-" in item_id or item_type == "M" else f"{item_id}-1"
        html, error = BrickLinkSource._get(
            f"{BrickLinkSource.BASE_URL}?{item_type}={suffix}#T=S")
        if not html:
            return None, error
        m = re.search(r"idItem['\"]?\s*[:=]\s*['\"]?(\d+)", html)
        if not m:
            return None, "no idItem on the catalog page"
        return int(m.group(1)), None

    @staticmethod
    def parse_offers(payload):
        """[{price, currency, condition, complete, description, qty, country}]
        from the offers endpoint's JSON."""
        try:
            rows = json.loads(payload).get("list") or []
        except (ValueError, AttributeError):
            return []
        out = []
        for r in rows:
            shown = r.get("mDisplaySalePrice") or ""
            m = re.search(r"([A-Z]{2,3})?\s*\$?\s*([\d,]+\.?\d*)", shown)
            if not m:
                continue
            try:
                price = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
            if price <= 0:
                continue
            code = (r.get("codeComplete") or "").upper()
            out.append({
                "price": price,
                "currency": (m.group(1) or "").upper() or None,
                "condition": "new" if (r.get("codeNew") or "").upper() == "N" else "used",
                # Sealed and complete both mean the whole item; B does not.
                "complete": code != BrickLinkSource._INCOMPLETE_CODE,
                "sealed": code == "S",
                "description": (r.get("strDesc") or "").strip(),
                "qty": int(r.get("n4Qty") or 1),
                "country": (r.get("strSellerCountryCode") or "").upper() or None,
            })
        return out

    def fetch_offers(self, item_id: str, internal_id: int, item_type: str = "S",
                     per_page: int = 200):
        """(offers, error) — every current listing for the item.

        `internal_id` comes from find_internal_id and is expected to be cached
        by the caller; this method never looks it up, so a scan costs one
        request per item rather than two.
        """
        payload, error = self._get_offers_json(internal_id, item_id,
                                               item_type, per_page)
        if not payload:
            return [], error
        offers = self.parse_offers(payload)
        # An item with no listings is a fact about the market, not a failure,
        # and must not read like one — that distinction is what 40 "no offers
        # parsed" lines hid.
        return offers, None

    def parse_minifig_inventory(self, html: str):
        """[{id, name, qty}, ...] from catalogItemInv.asp?...&viewItemType=M.
        Unlike the root scraper, real quantities are parsed.

        Data rows are the leaf <tr>s: * | Qty | Item No | Description. Two
        things this has to get right, both of which the previous version got
        wrong on every real page:

        * **The id is validated.** BrickLink links printed parts from the same
          `?M=` hrefs as minifigs — Ant-Man's statuette `90398pb007` sits in
          76051's fig inventory. Accepting one means scanning it as a fig,
          which times out on a page that does not exist, and counting it in
          every fig-value total.
        * **The name comes from the Description cell's own <b>**, not from the
          longest link text in the row. The old heuristic scanned every anchor
          and took the longest, which on a row containing a second item's id
          stored that id as the name — and because it walked wrapper <tr>s too,
          `links[0]` was not even this row's item.
        """
        from ..importer import item_type_for

        soup = BeautifulSoup(html, "html.parser")
        figs, seen = [], set()
        for tr in soup.find_all("tr"):
            if tr.find("tr"):
                continue                     # a wrapper row, not a data row
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue
            links = tr.find_all("a", href=re.compile(r"\?M="))
            if not links:
                continue
            m = re.search(r"\?M=([\w.-]+)", links[0]["href"])
            if not m:
                continue
            fig_id = m.group(1)
            if fig_id in seen or item_type_for(fig_id) != "M":
                continue                     # a printed part, not a minifig
            qty_txt = cells[1].get_text(strip=True)
            qty = int(qty_txt) if qty_txt.isdigit() else 1
            bold = cells[3].find("b")
            name = bold.get_text(" ", strip=True) if bold else ""
            if name == fig_id:
                name = ""
            seen.add(fig_id)
            figs.append({"id": fig_id, "name": name, "qty": max(1, qty)})
        return figs

    def fetch_minifig_inventory(self, set_id: str):
        url = f"{INV_URL}?S={set_id if '-' in set_id else set_id + '-1'}&viewItemType=M"
        html, error = self._get(url)
        if html:
            figs = self.parse_minifig_inventory(html)
            if figs:
                return figs, None
            error = "no minifigs parsed from inventory page"
        return [], error

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _get(url):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            resp.raise_for_status()
            # BrickLink's bot protection answers plain-HTTP clients with an
            # empty 202 on catalog pages; only a real browser gets the HTML.
            if resp.text.strip():
                return resp.text, None
            error = f"empty response (HTTP {resp.status_code})"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        html, browser_error = BrickLinkSource._get_browser(url)
        if html and not BrickLinkSource._is_error_page(html):
            return html, None
        if html:
            # Chrome renders its own page when a request fails, and returning
            # that as content made a refused request look like an item with
            # nothing to say: 40 sets in a row reported "no offers parsed"
            # when what had actually happened was that BrickLink stopped
            # answering us.
            browser_error = "browser returned an error page (blocked or offline)"
        return None, f"{error}; browser fallback: {browser_error}"

    # Chromium's built-in error pages carry this stylesheet variable and the
    # neterror body class; real BrickLink pages carry neither.
    _ERROR_PAGE_MARKERS = ("--error-code-color", "id=\"main-frame-error\"",
                           "chrome-error://", "ERR_")

    @staticmethod
    def _is_error_page(html):
        head = html[:4000]
        return any(m in head for m in BrickLinkSource._ERROR_PAGE_MARKERS)

    @staticmethod
    def _get_browser(url):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            opts = Options()
            opts.add_argument("--headless")
            opts.add_argument("--log-level=3")
            opts.add_argument(f"user-agent={USER_AGENT}")
            driver = webdriver.Chrome(options=opts)
            driver.set_page_load_timeout(30)
            try:
                driver.get(url)
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.TAG_NAME, "table")))
                except Exception:
                    pass  # not every catalog page renders a table
                return driver.page_source, None
            finally:
                driver.quit()
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    run_cli(BrickLinkSource())
