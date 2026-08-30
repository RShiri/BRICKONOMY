"""Sell / buy-window signals, and landed-cost deal margins — offline.

These tell someone to spend or realise money, so the property that matters most
is when they stay quiet. On the real database today they fire on nothing: 91 of
92 holdings have fewer than three price points, because scanning only began in
August. That is correct, and it is why the logic is proven here instead.
"""
from datetime import datetime, timedelta

import pytest

from brickonomy import db as dbq
from brickonomy.analytics import signals


@pytest.fixture()
def conn(tmp_path):
    from brickonomy.currency import reset_rate_cache
    reset_rate_cache()
    c = dbq.connect(db_path=str(tmp_path / "sig.db"))
    for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
        dbq.upsert_rate(c, "USD", q, r)
    dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars",
                    year=2017, item_type="S")
    c.commit()
    yield c
    c.close()


def history(conn, item_id, prices, days_apart=120, condition="new"):
    """Seed a blended series ending today."""
    start = datetime.now() - timedelta(days=days_apart * (len(prices) - 1))
    for n, p in enumerate(prices):
        dbq.insert_snapshot(
            conn, item_id, "blended", condition, "market", "ILS",
            market_price=p, confidence="HIGH",
            scraped_at=(start + timedelta(days=days_apart * n))
            .isoformat(timespec="seconds"))
    conn.commit()


def sells(conn, item_id, n, condition="new"):
    dbq.insert_snapshot(conn, item_id, "bricklink", condition, "sold", "ILS",
                        price_avg=100.0, listing_count=n, total_qty=n)
    conn.commit()


class TestSellSignalStaysQuiet:
    def test_silent_without_enough_history(self, conn):
        """Two points is not a trend. This is the case on almost every holding
        today, and a signal fired from it would be noise."""
        history(conn, "75192", [100.0, 101.0])
        sells(conn, "75192", 30)
        assert signals.sell_signal(conn, "75192", paid=50.0) is None

    def test_silent_on_a_low_confidence_value(self, conn):
        history(conn, "75192", [100.0, 101.0, 102.0])
        conn.execute("UPDATE price_snapshots SET confidence='LOW'")
        conn.commit()
        sells(conn, "75192", 30)
        assert signals.sell_signal(conn, "75192", paid=50.0) is None

    def test_silent_with_no_price_at_all(self, conn):
        assert signals.sell_signal(conn, "75192", paid=50.0) is None

    def test_does_not_fire_while_it_is_still_growing(self, conn):
        history(conn, "75192", [100.0, 140.0, 200.0])
        sells(conn, "75192", 30)
        s = signals.sell_signal(conn, "75192", paid=50.0)
        assert s["fire"] is False

    def test_does_not_fire_on_something_that_barely_trades(self, conn):
        """Telling someone to sell what nobody buys is not advice."""
        history(conn, "75192", [100.0, 100.5, 101.0])
        sells(conn, "75192", 1)
        assert signals.sell_signal(conn, "75192", paid=50.0)["fire"] is False

    def test_does_not_fire_at_a_loss(self, conn):
        history(conn, "75192", [100.0, 100.5, 101.0])
        sells(conn, "75192", 30)
        assert signals.sell_signal(conn, "75192", paid=500.0)["fire"] is False

    def test_does_not_fire_without_a_purchase_price(self, conn):
        history(conn, "75192", [100.0, 100.5, 101.0])
        sells(conn, "75192", 30)
        assert signals.sell_signal(conn, "75192", paid=None)["fire"] is False


class TestSellSignalFires:
    def test_flat_liquid_and_in_profit(self, conn):
        history(conn, "75192", [100.0, 100.5, 101.0])
        sells(conn, "75192", 30)
        s = signals.sell_signal(conn, "75192", paid=50.0)
        assert s["fire"] is True

    def test_it_states_its_own_numbers(self, conn):
        """A chip about real money has to be able to explain itself."""
        history(conn, "75192", [100.0, 100.5, 101.0])
        sells(conn, "75192", 30)
        s = signals.sell_signal(conn, "75192", paid=50.0)
        assert len(s["reasons"]) == 3
        assert any("flattened" in r for r in s["reasons"])
        assert any("30×" in r for r in s["reasons"])
        assert any("up +" in r for r in s["reasons"])
        assert s["points"] == 3 and s["confidence"] == "HIGH"


class TestBuyWindow:
    def test_fires_when_retirement_is_within_a_year(self, conn):
        year = datetime.now().year - 2          # +3 heuristic -> next year
        w = signals.buy_window(conn, "75192", "Star Wars", year)
        assert w["fire"] is True and w["years_left"] == 1

    def test_quiet_for_a_set_years_from_retiring(self, conn):
        w = signals.buy_window(conn, "75192", "Star Wars", datetime.now().year)
        assert w["fire"] is False

    def test_quiet_for_a_set_already_retired(self, conn):
        w = signals.buy_window(conn, "75192", "Star Wars", 2010)
        assert w["fire"] is False, "the window shut years ago"

    def test_nothing_claimed_without_a_release_year(self, conn):
        assert signals.buy_window(conn, "75192", "Star Wars", None) is None

    def test_the_date_is_declared_an_estimate(self, conn):
        """Every retirement year is release + a flat allowance. The chip must
        not imply a published date."""
        w = signals.buy_window(conn, "75192", "Star Wars",
                               datetime.now().year - 2)
        assert w["estimated"] is True


class TestLandedCostDeals:
    def _deal(self, conn, price, currency):
        from brickonomy.web.app import deal_for
        dbq.insert_snapshot(conn, "75192", "blended", "new", "market", "ILS",
                            market_price=1000.0, confidence="HIGH")
        dbq.insert_snapshot(conn, "75192", "ebay", "new", "stock", currency,
                            price_min=price,
                            raw=[{"price": price, "qty": 1, "description": ""}])
        conn.commit()
        return deal_for(conn, "75192", "ILS")

    def test_a_domestic_listing_is_judged_on_its_price(self, conn):
        d = self._deal(conn, 500.0, "ILS")
        assert d["landed"] == pytest.approx(500.0)
        assert d["imported"] is False and d["import_extra"] == 0.0

    def test_an_import_is_judged_on_what_it_costs_delivered(self, conn):
        """The observed case: a $162 listing reads as ₪1,437 landed, which is
        no deal on a set worth ₪486 however cheap the sticker price looks."""
        d = self._deal(conn, 150.0, "USD")       # ₪525 + VAT + shipping
        assert d["imported"] is True
        assert d["landed"] > d["ask"]
        assert d["import_extra"] > 0
        assert d["margin"] < (1000.0 - d["ask"] * 1.13) / d["ask"] * 100.0, \
            "ignoring import costs would have flattered the margin"

    def test_import_costs_can_disqualify_a_deal(self, conn):
        d = self._deal(conn, 250.0, "USD")       # ₪875 + VAT + ship > ₪1,000
        assert d["rating"] == "IRRELEVANT"
