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
