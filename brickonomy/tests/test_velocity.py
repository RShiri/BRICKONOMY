"""Sales velocity, and the snapshot compaction that keeps history honest."""
from datetime import datetime, timedelta

import pytest

from brickonomy import db as dbq
from brickonomy.analytics import velocity as vel
from brickonomy.cleanup import compact_snapshots, compactable_runs


@pytest.fixture()
def conn(tmp_path):
    c = dbq.connect(db_path=str(tmp_path / "v.db"))
    dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars", item_type="S")
    c.commit()
    yield c
    c.close()


def sold(conn, item_id, sales, units=None, condition="new", source="bricklink"):
    dbq.insert_snapshot(conn, item_id, source, condition, "sold", "ILS",
                        price_avg=100.0, listing_count=sales,
                        total_qty=units if units is not None else sales)
    conn.commit()


class TestVelocity:
    def test_reads_the_sold_counts_the_scraper_already_stored(self, conn):
        sold(conn, "75192", sales=10, units=11)
        v = vel.velocity(conn, "75192")
        assert v["sales"] == 10 and v["units"] == 11
        assert v["per_month"] == pytest.approx(1.7, abs=0.05)

    @pytest.mark.parametrize("sales,band", [
        (52, "weekly"), (26, "weekly"), (12, "monthly"),
        (6, "monthly"), (3, "slow"), (2, "slow"), (1, "rare"), (0, "rare"),
    ])
    def test_bands(self, sales, band):
        assert vel.band_for(sales)[0] == band

    def test_no_sold_data_is_none_not_zero(self, conn):
        """'Never scanned' and 'never sells' are different claims, and the UI
        must not print the first as the second."""
        assert vel.velocity(conn, "75192") is None
        dbq.insert_snapshot(conn, "75192", "bricklink", "new", "market", "ILS",
                            market_price=100.0)
        conn.commit()
        assert vel.velocity(conn, "75192") is None, "a market row is not a sold row"

    def test_conditions_are_tracked_separately(self, conn):
        sold(conn, "75192", sales=30, condition="new")
        sold(conn, "75192", sales=2, condition="used")
        assert vel.velocity(conn, "75192", "new")["band"] == "weekly"
        assert vel.velocity(conn, "75192", "used")["band"] == "slow"

    def test_ebay_volume_is_ignored(self, conn):
        """eBay's sold count comes from a paginated signed-in search, so it
        measures the scrape rather than the market."""
        sold(conn, "75192", sales=40, source="ebay")
        assert vel.velocity(conn, "75192") is None

    def test_liquidity_discounts_a_paper_margin(self):
        assert vel.liquidity_factor({"band": "weekly"}) == 1.0
        assert vel.liquidity_factor({"band": "rare"}) == 0.5
        # Unknown is cautious, not optimistic.
        assert 0.5 < vel.liquidity_factor(None) < 1.0

    def test_a_fast_thin_margin_beats_a_fat_illiquid_one(self):
        fast = 30 * vel.liquidity_factor({"band": "weekly"})
        slow = 45 * vel.liquidity_factor({"band": "rare"})
        assert fast > slow


class TestCompaction:
    def _burst(self, conn, prices):
        base = datetime(2026, 1, 1, 12, 0, 0)
        for n, p in enumerate(prices):
            dbq.insert_snapshot(
                conn, "75192", "bricklink", "new", "market", "ILS",
                market_price=p,
                scraped_at=(base + timedelta(days=n)).isoformat(timespec="seconds"))
        conn.commit()

    def test_a_run_of_identical_prices_keeps_only_its_ends(self, conn):
        self._burst(conn, [100.0, 100.0, 100.0, 100.0, 100.0])
        assert len(compactable_runs(conn)) == 1
        removed = compact_snapshots(conn, log=lambda *a: None)
        assert removed == 3
        left = conn.execute(
            "SELECT scraped_at FROM price_snapshots ORDER BY scraped_at").fetchall()
        assert len(left) == 2
        assert left[0]["scraped_at"].startswith("2026-01-01")
        assert left[1]["scraped_at"].startswith("2026-01-05"), \
            "the last row must survive — it is when the price was still true"

    def test_changes_are_never_collapsed(self, conn):
        self._burst(conn, [100.0, 100.0, 120.0, 120.0, 100.0])
        before = [r["market_price"] for r in conn.execute(
            "SELECT market_price FROM price_snapshots ORDER BY scraped_at")]
        compact_snapshots(conn, log=lambda *a: None)
        after = [r["market_price"] for r in conn.execute(
            "SELECT market_price FROM price_snapshots ORDER BY scraped_at")]
        assert before == after, "no run reaches three, so nothing is removable"

    def test_the_chart_series_is_unchanged(self, conn):
        """The whole point: compaction must be invisible in the history. The
        chart reads the blended series, so that is what this checks."""
        from brickonomy.analytics import growth
        base = datetime(2026, 1, 1, 12, 0, 0)
        for n, p in enumerate([100.0, 100.0, 100.0, 100.0, 130.0, 130.0, 130.0]):
            dbq.insert_snapshot(
                conn, "75192", "blended", "new", "market", "ILS", market_price=p,
                scraped_at=(base + timedelta(days=n)).isoformat(timespec="seconds"))
        conn.commit()
        before = growth.series(conn, "75192")
        compact_snapshots(conn, log=lambda *a: None)
        after = growth.series(conn, "75192")
        assert [p[1] for p in after] == [100.0, 100.0, 130.0, 130.0]
        assert after[0] == before[0] and after[-1] == before[-1], \
            "the endpoints — first seen and last seen — are preserved exactly"

    def test_different_series_never_merge(self, conn):
        """Same price, different source or condition, must stay separate."""
        for src in ("bricklink", "ebay"):
            for cond in ("new", "used"):
                for n in range(4):
                    dbq.insert_snapshot(
                        conn, "75192", src, cond, "market", "ILS",
                        market_price=100.0,
                        scraped_at=f"2026-01-0{n + 1}T12:00:00")
        conn.commit()
        assert len(compactable_runs(conn)) == 4, "one run per (source, condition)"
        compact_snapshots(conn, log=lambda *a: None)
        assert conn.execute(
            "SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"] == 8

    def test_dry_run_removes_nothing(self, conn):
        self._burst(conn, [100.0] * 5)
        assert compact_snapshots(conn, dry_run=True, log=lambda *a: None) == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"] == 5

    def test_running_twice_is_a_no_op(self, conn):
        self._burst(conn, [100.0] * 5)
        compact_snapshots(conn, log=lambda *a: None)
        assert compact_snapshots(conn, log=lambda *a: None) == 0


class TestDealPlausibility:
    """BrickOwl matches some set numbers against parts sharing the number
    (11211 is a set *and* a very common brick), producing sub-agora "sets"
    that otherwise top the deals list at millions of percent."""

    def _deal(self, conn, ask):
        from brickonomy.web.app import deal_for
        dbq.insert_snapshot(conn, "75192", "blended", "new", "market", "ILS",
                            market_price=1000.0)
        # cheapest_stock reads the retained raw listings, not the aggregate
        # columns, so the fixture has to carry them.
        dbq.insert_snapshot(conn, "75192", "brickowl", "new", "stock", "ILS",
                            price_min=ask, price_avg=ask, listing_count=1,
                            raw=[{"price": ask, "qty": 1,
                                  "description": "a listing"}])
        dbq.upsert_rate(conn, "USD", "ILS", 3.5)
        conn.commit()
        return deal_for(conn, "75192", "ILS")

    def test_an_impossible_ask_is_rejected(self, conn):
        assert self._deal(conn, 0.03) is None

    def test_a_steep_but_real_discount_still_qualifies(self, conn):
        """60% off is a great deal, not a mismatch — the floor must not eat it."""
        d = self._deal(conn, 400.0)
        assert d is not None and d["rating"] in ("GOOD", "EXCELLENT")

    def test_the_ranking_is_discounted_by_liquidity(self, conn):
        dbq.insert_snapshot(conn, "75192", "bricklink", "new", "sold", "ILS",
                            price_avg=1000.0, listing_count=1, total_qty=1)
        conn.commit()
        d = self._deal(conn, 400.0)
        assert d["adjusted_margin"] < d["margin"], "barely-trades is discounted"
        assert d["velocity"]["band"] == "rare"
