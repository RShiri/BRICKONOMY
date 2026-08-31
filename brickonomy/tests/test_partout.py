"""The part-out leaderboard — offline.

The numbers here tell someone to spend money, so the properties under test are
the ones that stop it lying: costs include what delivery really adds, yield is
what plausibly arrives rather than the theoretical maximum, and a listing that
is not the set does not become the top opportunity.
"""
import pytest

from brickonomy import db as dbq
from brickonomy.analytics import partout
from brickonomy.analytics.listings import (drop_lone_lowball, landed_cost,
                                           looks_like_a_component)


@pytest.fixture()
def conn(tmp_path):
    from brickonomy.currency import reset_rate_cache
    reset_rate_cache()          # rates are cached module-wide between calls
    c = dbq.connect(db_path=str(tmp_path / "po.db"))
    # Every quote has to be seeded, not just the one under test: the loader
    # treats a partial set as stale and falls through to a live API call,
    # which would make these assertions depend on today's exchange rate.
    dbq.upsert_rate(c, "USD", "ILS", 3.5)
    dbq.upsert_rate(c, "USD", "EUR", 0.9)
    dbq.upsert_rate(c, "USD", "GBP", 0.8)
    dbq.upsert_item(c, "76060", name="Sanctum", theme="Super Heroes Marvel",
                    year=2016, parts=358, item_type="S")
    dbq.insert_snapshot(c, "76060", "blended", "new", "market", "ILS",
                        market_price=120.0)
    dbq.upsert_part_out(c, "76060", 300.0, "ILS")
    c.commit()
    yield c
    c.close()


def listing(conn, item_id, price, currency="ILS", source="bricklink",
            description=""):
    dbq.insert_snapshot(conn, item_id, source, "new", "stock", currency,
                        price_min=price, raw=[{"price": price, "qty": 1,
                                               "description": description}])
    conn.commit()


class TestYieldIsHonest:
    def test_the_yield_is_not_the_part_out_total(self, conn):
        """A part-out total assumes every brick eventually sells. It doesn't."""
        listing(conn, "76060", 50.0)
        r = partout.opportunities(conn)[0]
        assert r["pov"] == pytest.approx(300.0)
        assert r["revenue"] < r["pov"]
        expected = 300.0 * partout.REALISATION_RATE * (1 - partout.SELLER_FEE_RATE)
        assert r["revenue"] == pytest.approx(expected)

    def test_profit_is_measured_against_landed_cost(self, conn):
        listing(conn, "76060", 50.0)
        r = partout.opportunities(conn)[0]
        assert r["profit"] == pytest.approx(r["revenue"] - r["cost"])
        assert r["margin"] == pytest.approx(r["profit"] / r["cost"] * 100)


class TestLandedCost:
    def test_a_domestic_listing_costs_its_price(self, conn):
        cost, extra = landed_cost(conn, 100.0, "ILS")
        assert cost == pytest.approx(100.0) and extra == 0.0

    def test_an_import_carries_vat_and_shipping(self, conn):
        """A $100 listing is not ₪350 delivered."""
        cost, extra = landed_cost(conn, 100.0, "USD")
        assert cost > 350.0
        assert extra == pytest.approx(cost - 350.0)

    def test_the_cheapest_row_is_chosen_after_delivery_not_before(self, conn):
        """A cheaper sticker price can land dearer than a domestic listing."""
        listing(conn, "76060", 100.0, "ILS", source="bricklink")
        listing(conn, "76060", 25.0, "USD", source="ebay")   # 87.50 + VAT + ship
        r = partout.opportunities(conn)[0]
        assert r["source"] == "bricklink", "the domestic one is cheaper delivered"
        assert not r["imported"]


class TestMismatchedListings:
    @pytest.mark.parametrize("desc", [
        "New LEGO Sticker Sheet for Set 5002145",
        "New Black Wheel Rim Ø14.6 x 9.9",
        "Replacement part for set",
    ])
    def test_component_listings_are_recognised(self, desc):
        assert looks_like_a_component(desc)

    @pytest.mark.parametrize("desc", [
        "LEGO Marvel Super Heroes 76060 Doctor Strange Sanctum Sanctorum",
        "New sealed set, box has shelf wear",
        "",
    ])
    def test_real_set_listings_are_not(self, desc):
        assert not looks_like_a_component(desc)

    def test_a_sticker_sheet_does_not_become_the_top_opportunity(self, conn):
        """The observed failure: a ₪1.65 sticker sheet for a ₪49 polybag ranked
        first at a four-figure margin."""
        listing(conn, "76060", 1.65, description="New LEGO Sticker Sheet for Set 76060")
        assert partout.opportunities(conn) == []

    def test_an_absurdly_cheap_listing_is_rejected(self, conn):
        listing(conn, "76060", 0.03)
        assert partout.opportunities(conn) == []

    def test_a_steep_but_real_discount_survives(self, conn):
        listing(conn, "76060", 40.0)          # 33% of a ₪120 set
        assert len(partout.opportunities(conn)) == 1


class TestSignals:
    def test_sets_worth_more_whole_are_flagged(self, conn):
        """Breaking up a set that sells for more intact is the worse trade."""
        dbq.insert_snapshot(conn, "76060", "blended", "new", "market", "ILS",
                            market_price=500.0)
        listing(conn, "76060", 50.0)
        conn.commit()
        r = partout.opportunities(conn)[0]
        assert r["better_whole"] is True

    def test_ranking_is_discounted_by_how_often_the_set_sells(self, conn):
        listing(conn, "76060", 50.0)
        dbq.insert_snapshot(conn, "76060", "bricklink", "new", "sold", "ILS",
                            price_avg=120.0, listing_count=1, total_qty=1)
        conn.commit()
        r = partout.opportunities(conn)[0]
        assert r["adjusted_margin"] < r["margin"]
        assert r["velocity"]["band"] == "rare"

    def test_a_budget_filters_on_landed_cost(self, conn):
        listing(conn, "76060", 100.0)
        assert partout.opportunities(conn, max_budget=50.0) == []
        assert len(partout.opportunities(conn, max_budget=150.0)) == 1

    def test_stale_part_out_data_is_flagged_not_dropped(self, conn):
        listing(conn, "76060", 50.0)
        conn.execute("UPDATE part_out SET scraped_at='2026-01-01T00:00:00'")
        conn.commit()
        rows = partout.opportunities(conn)
        assert rows and rows[0]["stale"] is True
        assert partout.opportunities(conn, include_stale=False) == []

    def test_figure_share_is_none_when_no_figure_is_priced(self, conn):
        """Zero would read as 'no value in figures', which is a different and
        wrong claim."""
        listing(conn, "76060", 50.0)
        dbq.upsert_set_minifigs(conn, "76060", [{"id": "sh0170", "name": "Strange",
                                                 "qty": 1}])
        conn.commit()
        r = partout.opportunities(conn)[0]
        assert r["fig_share"] is None and r["figs_total"] == 1


class TestRetirementIsLabelledAsAGuess:
    """No availability source is wired up, so every retirement year is a flat
    +2/+3 heuristic. It must not be presented as a published date."""

    def test_every_dated_phase_is_flagged_estimated(self):
        from brickonomy.analytics import lifecycle
        for year in (2016, 2021, 2026):
            assert lifecycle.phase(year, "Icons")["retirement_estimated"] is True

    def test_an_undated_item_claims_nothing(self):
        from brickonomy.analytics import lifecycle
        ph = lifecycle.phase(None)
        assert ph["retirement_year"] is None
        assert ph["retirement_estimated"] is False


class TestUnderMarketListingsAreWholeSets:
    """An under-market price is only a deal if the thing on sale is the set.
    eBay searches free text and BrickOwl matches numbers across item types, so
    both surface figures, helmets and sticker sheets under a set number."""

    @pytest.mark.parametrize("desc", [
        # All observed live in the deals list.
        "LEGO - Minifigs - Super Heroes - sh1131 - Phil Coulson (76354)",
        "LEGO Spider Man Green Goblin Minifigure HELMET Spd006 4851 4852",
        "New LEGO SHIELD Agent Statuette Minifigure 20 ILS",
        "New LEGO Sticker Sheet for Set 5002145",
        "LEGO 75192 instructions only, no bricks",
        "Empty box for 76060",
    ])
    def test_a_part_or_figure_is_not_a_set(self, desc):
        assert looks_like_a_component(desc, "S")

    @pytest.mark.parametrize("desc", [
        "LEGO Marvel 76060 Doctor Strange Sanctum Sanctorum, 4 minifigures",
        "New sealed LEGO 75192 Millennium Falcon UCS, box has shelf wear",
        "LEGO 76049 Avenjet - complete with all minifigs and instructions",
    ])
    def test_a_complete_set_survives_even_when_it_mentions_minifigures(self, desc):
        """Sets legitimately advertise the figures they contain; that must not
        be read as the listing *being* a figure."""
        assert not looks_like_a_component(desc, "S")

    def test_a_figure_id_is_expected_on_a_figure_page(self):
        assert not looks_like_a_component(
            "LEGO Minifigs sh1131 Phil Coulson", "M")

    def test_bricklink_listings_are_trusted_without_a_description(self):
        """BrickLink is fetched at catalogitem.page?S=<id>, pinned to the Set
        item type, so its listings are the set by construction — which is why
        it stores no description and needs none."""
        assert not looks_like_a_component("", "S")
        assert not looks_like_a_component(None, "S")


class TestLoneLowball:
    """BrickLink's guide for 76060 carried 60 asks with a ₪150 median and a
    lowest of ₪33.63 — 22% of the median, 41% under the next one up. That row
    put the set top of the part-out leaderboard at a 424% margin."""

    def _offers(self, prices):
        return [{"price": p, "description": ""} for p in prices]

    def test_an_isolated_lowball_is_dropped(self):
        # The real 76060 shape.
        kept = drop_lone_lowball(self._offers(
            [33.63, 57.43, 93.87, 105.06, 114.86, 150.0, 150.0, 195.26]))
        assert min(o["price"] for o in kept) == 57.43

    def test_two_sellers_at_the_same_low_price_are_a_market(self):
        # 10782: two independent asks at ₪12 against a ₪67 median. Thin, but
        # real — and exactly the bargain the app exists to surface.
        prices = [12.0, 12.0, 60.0, 67.65, 70.0, 90.0, 110.0]
        kept = drop_lone_lowball(self._offers(prices))
        assert min(o["price"] for o in kept) == 12.0

    def test_a_merely_cheap_ask_is_kept(self):
        # 60% of the median, with company just above it: a discount, not an
        # error.
        kept = drop_lone_lowball(self._offers([60.0, 70.0, 95.0, 100.0, 105.0, 110.0]))
        assert min(o["price"] for o in kept) == 60.0

    def test_two_bad_rows_in_a_row_both_go(self):
        kept = drop_lone_lowball(self._offers(
            [1.0, 2.0, 100.0, 105.0, 110.0, 115.0, 120.0]))
        assert min(o["price"] for o in kept) == 100.0

    def test_too_few_asks_to_have_an_opinion(self):
        # With three asks there is no median worth trusting, so nothing is
        # second-guessed.
        kept = drop_lone_lowball(self._offers([5.0, 100.0, 110.0]))
        assert min(o["price"] for o in kept) == 5.0

    def test_it_never_empties_the_list(self):
        kept = drop_lone_lowball(self._offers([1.0, 2.0, 3.0, 4.0, 5.0]))
        assert kept, "something must survive"
