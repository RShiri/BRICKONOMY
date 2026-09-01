"""Whole-item listings.

BrickLink's price guide reports the asks for an item without saying whether
each is for the whole thing. Set 76049's cheapest "listing" was ILS 11.88 for
Captain America's backpack, and the buy signal quoted it as 53% under market;
the cheapest listing that is actually the set is ILS 327.65.
"""
import json

import pytest

from brickonomy import db as dbq
from brickonomy.analytics.listings import cheapest_stock, looks_like_a_component
from brickonomy.scrapers.bricklink import BrickLinkSource


def payload(rows):
    return json.dumps({"total_count": len(rows), "list": rows})


def offer(price, code_complete, code_new="U", desc="", country="US"):
    return {"mDisplaySalePrice": f"ILS {price}", "codeComplete": code_complete,
            "codeNew": code_new, "strDesc": desc, "n4Qty": "1",
            "strSellerCountryCode": country}


class TestParseOffers:
    def test_the_completeness_code_is_read(self):
        rows = BrickLinkSource.parse_offers(payload([
            offer("11.88", "B", desc="Capt America's backpack only"),
            offer("327.65", "C", desc="complete with instructions, no box"),
            offer("419.65", "S", "N"),
        ]))
        assert [o["complete"] for o in rows] == [False, True, True]
        assert [o["sealed"] for o in rows] == [False, False, True]

    def test_condition_comes_from_the_new_code(self):
        rows = BrickLinkSource.parse_offers(payload([
            offer("10", "C", "N"), offer("10", "C", "U")]))
        assert [o["condition"] for o in rows] == ["new", "used"]

    def test_the_sellers_own_words_are_kept(self):
        """The price guide carries no description at all, which is why the
        text filter could never help there."""
        rows = BrickLinkSource.parse_offers(payload([
            offer("38.10", "B", desc="No Minifigures, or weapons")]))
        assert rows[0]["description"] == "No Minifigures, or weapons"

    def test_the_sellers_country_is_kept(self):
        rows = BrickLinkSource.parse_offers(payload([offer("10", "C", country="ca")]))
        assert rows[0]["country"] == "CA"

    def test_a_free_or_broken_price_is_dropped(self):
        rows = BrickLinkSource.parse_offers(payload([
            offer("0.00", "C"), {"mDisplaySalePrice": "", "codeComplete": "C"}]))
        assert rows == []

    def test_junk_is_not_an_exception(self):
        assert BrickLinkSource.parse_offers("not json") == []
        assert BrickLinkSource.parse_offers("{}") == []


class TestCheapestPrefersWholeItems:
    @pytest.fixture()
    def conn(self, tmp_path):
        c = dbq.connect(db_path=str(tmp_path / "o.db"))
        dbq.upsert_item(c, "76049", name="Avenjet", item_type="S")
        yield c
        c.close()

    def test_a_piece_of_the_set_is_not_its_cheapest_listing(self, conn):
        dbq.insert_snapshot(conn, "76049", "bricklink", "used", "offers", "ILS",
                            raw=[{"price": 11.88, "complete": False,
                                  "description": "Capt America's backpack only"},
                                 {"price": 327.65, "complete": True,
                                  "description": "complete with instructions"}])
        conn.commit()
        assert cheapest_stock(conn, "76049", "used")["bricklink"]["price"] == 327.65

    def test_offers_win_over_the_price_guide(self, conn):
        """Both are stored; the guide cannot tell a backpack from a set."""
        dbq.insert_snapshot(conn, "76049", "bricklink", "used", "stock", "ILS",
                            raw=[{"price": 38.10, "description": ""}])
        dbq.insert_snapshot(conn, "76049", "bricklink", "used", "offers", "ILS",
                            raw=[{"price": 327.65, "complete": True,
                                  "description": ""}])
        conn.commit()
        assert cheapest_stock(conn, "76049", "used")["bricklink"]["price"] == 327.65

    def test_the_guide_still_answers_where_no_offers_were_scraped(self, conn):
        dbq.insert_snapshot(conn, "76049", "bricklink", "used", "stock", "ILS",
                            raw=[{"price": 200.0, "description": ""}])
        conn.commit()
        assert cheapest_stock(conn, "76049", "used")["bricklink"]["price"] == 200.0

    def test_an_absent_flag_is_not_treated_as_incomplete(self, conn):
        """Every row scraped before the offers endpoint existed lacks the
        flag; those are unknown, not known-incomplete."""
        dbq.insert_snapshot(conn, "76049", "bricklink", "used", "stock", "ILS",
                            raw=[{"price": 150.0, "description": ""}])
        conn.commit()
        assert cheapest_stock(conn, "76049", "used")["bricklink"]["price"] == 150.0


class TestInstructionListings:
    """eBay's category echo puts "Instructions / Instruction" in the title of
    a listing that is only the booklet. "Complete with instructions" is a
    whole set and has to survive."""

    @pytest.mark.parametrize("text", [
        "Lego 76049 - Avengers Avenger - Instructions / Instruction",
        "LEGO Instruction Book 76031 Marvel Super Heroes",
        "LEGO instructions/instruction no. 4855",
        "76014 Spider Trike vs Electro - Instructions / Instruction",
    ])
    def test_an_instruction_sale_is_rejected(self, text):
        assert looks_like_a_component(text) is True

    @pytest.mark.parametrize("text", [
        "Used (Complete) LEGO Thanos Mech Set 76141 - INSTRUCTIONS",
        "Iron Man Mech Armor - Lego Set 76203 - Instructions / Complete / VGC",
        "complete with instructions, no box",
        "Complete with all minifigures. Stickers applied",
        "LEGO 76049 sealed new in box",
    ])
    def test_a_whole_set_that_mentions_instructions_survives(self, text):
        assert looks_like_a_component(text) is False


class TestOffersCountAsWork:
    """An item whose prices are fresh still needs its whole-item listings, or
    its cheapest-listing figure keeps coming from the price guide."""

    def _seed(self, tmp_path, with_offers):
        from brickonomy.config import get_config
        path = str(tmp_path / "w.db")
        c = dbq.connect(db_path=path)
        dbq.upsert_item(c, "76049", name="Avenjet", item_type="S")
        dbq.upsert_set_parts(c, "76049", [{"part_no": "3001",
                                           "part_name": "Brick 2 x 4",
                                           "color_id": 5, "color_name": "Red",
                                           "qty": 1}])
        for src in get_config().sources_enabled:
            dbq.insert_snapshot(c, "76049", src, "new", "stock", "ILS",
                                price_avg=10.0)
        if with_offers:
            dbq.insert_snapshot(c, "76049", "bricklink", "used", "offers",
                                "ILS", raw=[{"price": 10.0, "complete": True}])
        c.commit()
        return c

    def test_a_fresh_item_without_offers_still_needs_scanning(self, tmp_path):
        from brickonomy.config import get_config
        from brickonomy.refresh import _needs_work
        c = self._seed(tmp_path, with_offers=False)
        try:
            assert _needs_work(c, get_config(), "76049", "S", False, False) is True
        finally:
            c.close()

    def test_once_it_has_them_it_is_finished(self, tmp_path):
        from brickonomy.config import get_config
        from brickonomy.refresh import _needs_work
        c = self._seed(tmp_path, with_offers=True)
        try:
            assert _needs_work(c, get_config(), "76049", "S", False, False) is False
        finally:
            c.close()

    def test_the_offers_scope_finds_priced_items_that_lack_them(self, tmp_path):
        from brickonomy.refresh import select_targets
        c = dbq.connect(db_path=str(tmp_path / "s.db"))
        try:
            for iid in ("76049", "75192"):
                dbq.upsert_item(c, iid, name="Set", item_type="S")
                dbq.insert_snapshot(c, iid, "blended", "new", "market", "ILS",
                                    market_price=100.0)
            dbq.insert_snapshot(c, "75192", "bricklink", "used", "offers",
                                "ILS", raw=[{"price": 10.0, "complete": True}])
            # An unpriced item cannot show a buy signal, so it is not the point.
            dbq.upsert_item(c, "10283", name="Unpriced", item_type="S")
            c.commit()
            assert [i for i, _ in select_targets(c, scope="offers")] == ["76049"]
        finally:
            c.close()


class TestFailuresAreDistinguishable:
    """A refused request and an item nobody is selling are different things,
    and reporting both as "no offers parsed" is what let 40 sets in a row
    record silence for what was really a 403."""

    def test_an_empty_market_is_not_an_error(self):
        assert BrickLinkSource.parse_offers(payload([])) == []

    def test_a_chromium_error_page_is_not_content(self):
        """_get fell back to a browser, the browser rendered its own error
        page, and that came back as if BrickLink had answered."""
        err_page = ('<html><head><style>body { --error-code-color: '
                    'var(--google-gray-700); }</style></head>'
                    '<body id="main-frame-error">ERR_FAILED</body></html>')
        assert BrickLinkSource._is_error_page(err_page) is True

    def test_a_real_answer_is_not_mistaken_for_one(self):
        assert BrickLinkSource._is_error_page('{"total_count": 58}') is False
        assert BrickLinkSource._is_error_page(
            "<html><body>Avenjet Space Mission</body></html>") is False
