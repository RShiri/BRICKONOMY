"""Priority scan ordering and the cross-process scan lock — offline."""
from datetime import datetime, timedelta

import pytest

from brickonomy import db as dbq
from brickonomy.refresh import PRIORITY_TIERS, scan_lock, select_targets


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    from brickonomy.config import get_config
    c = dbq.connect(db_path=str(tmp_path / "sched.db"))
    monkeypatch.setattr(get_config(), "db_path", str(tmp_path / "sched.db"))

    # One owned set, with a fig inside it; one wishlist set; one more set in a
    # theme that is collected; and one set in a theme nobody here owns.
    dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars", item_type="S")
    dbq.upsert_portfolio(c, "75192", owned=1)
    dbq.upsert_item(c, "sw0879", name="Han", item_type="M")
    dbq.upsert_set_minifigs(c, "75192", [{"id": "sw0879", "name": "Han", "qty": 1}])

    dbq.upsert_item(c, "75252", name="Star Destroyer", theme="Star Wars", item_type="S")
    dbq.upsert_portfolio(c, "75252", owned=0, wanted=1)

    dbq.upsert_item(c, "75300", name="TIE Fighter", theme="Star Wars", item_type="S")
    dbq.upsert_item(c, "10283", name="Shuttle", theme="Icons", item_type="S")
    c.commit()
    yield c
    c.close()


def tier_of(conn, item_id):
    order = [i for i, _ in select_targets(conn, "priority")]
    return order.index(item_id)


class TestPriorityOrdering:
    def test_tiers_run_owned_wishlist_figs_theme_catalog(self, conn):
        """The nightly budget must be spent on the collection before the rest
        of the catalog — `gaps` walks by id and would take weeks to reach the
        figures sitting inside owned sets."""
        order = [i for i, _ in select_targets(conn, "priority")]
        assert order.index("75192") < order.index("75252"), "owned before wishlist"
        assert order.index("75252") < order.index("sw0879"), "wishlist before its figs"
        assert order.index("sw0879") < order.index("75300"), "owned figs before held theme"
        assert order.index("75300") < order.index("10283"), "held theme before catalog"

    def test_never_scanned_comes_before_stale_within_a_tier(self, conn):
        """An interrupted run should have priced something new, not re-priced
        what it already knew."""
        old = (datetime.now() - timedelta(days=90)).isoformat(timespec="seconds")
        dbq.upsert_item(conn, "75999", name="Other owned", theme="Star Wars")
        dbq.upsert_portfolio(conn, "75999", owned=1)
        dbq.insert_snapshot(conn, "75192", "bricklink", "new", "market", "ILS",
                            market_price=100.0, scraped_at=old)
        conn.commit()
        order = [i for i, _ in select_targets(conn, "priority")]
        assert order.index("75999") < order.index("75192"), \
            "never-scanned owned set outranks a stale one"

    def test_every_catalog_item_is_reachable(self, conn):
        """No tier may drop rows: the last tier is the catalog remainder."""
        order = [i for i, _ in select_targets(conn, "priority")]
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        assert len(order) == total
        assert len(set(order)) == total, "no duplicates across tiers"

    def test_tier_labels_cover_the_sql_cases(self):
        assert set(PRIORITY_TIERS) == {1, 2, 3, 4, 5}


class TestScanLock:
    def test_a_second_holder_is_refused(self, tmp_path):
        path = tmp_path / "s.lock"
        with scan_lock(path) as first:
            assert first is True
            with scan_lock(path) as second:
                assert second is False, "one scraper at a time"

    def test_the_lock_is_released_afterwards(self, tmp_path):
        path = tmp_path / "s.lock"
        with scan_lock(path) as first:
            assert first is True
        with scan_lock(path) as again:
            assert again is True, "a finished run must not block the next one"

    def test_an_exception_still_releases_it(self, tmp_path):
        """A crashed scan must not wedge every future night."""
        path = tmp_path / "s.lock"
        with pytest.raises(RuntimeError):
            with scan_lock(path) as got:
                assert got is True
                raise RuntimeError("scraper blew up")
        with scan_lock(path) as again:
            assert again is True


class TestFigExpansion:
    """A set's inventory is only discovered while scanning it, so figures
    found mid-run have to join the same run or they wait a whole night."""

    def _run(self, conn, monkeypatch, tmp_path, **kw):
        import brickonomy.refresh as rf
        from brickonomy.config import get_config
        monkeypatch.setattr(get_config(), "fixture_mode", True)   # no sleeps
        monkeypatch.setattr(rf.dbq, "connect", lambda *a, **k: conn)
        seen = []
        monkeypatch.setattr(rf, "refresh_item",
                            lambda c, iid, itype=None, **kwargs: (seen.append(iid), {})[1])
        # run_refresh closes the connection it was handed; closing again in
        # the fixture teardown is a no-op, so nothing needs patching out.
        rf.run_refresh(log=lambda *a: None, **kw)
        return seen

    def test_figs_of_a_scanned_set_join_the_same_run(self, conn, monkeypatch, tmp_path):
        seen = self._run(conn, monkeypatch, tmp_path, scope="priority")
        assert "75192" in seen
        assert "sw0879" in seen, "the fig inside the owned set gets priced too"
        assert seen.index("75192") < seen.index("sw0879")

    def test_no_figs_flag_scans_only_the_listed_targets(self, conn, monkeypatch, tmp_path):
        seen = self._run(conn, monkeypatch, tmp_path, scope="portfolio",
                         with_figs=False)
        assert "75192" in seen and "sw0879" not in seen

    def test_the_limit_is_a_budget_for_the_whole_run(self, conn, monkeypatch,
                                                     tmp_path):
        """A fig-heavy set must not silently triple a night's work."""
        seen = self._run(conn, monkeypatch, tmp_path, scope="priority", limit=2)
        assert len(seen) <= 2

    def test_already_priced_figs_are_not_requeued(self, conn, monkeypatch, tmp_path):
        from brickonomy.refresh import unpriced_figs_of
        assert unpriced_figs_of(conn, "75192") == ["sw0879"]
        dbq.insert_snapshot(conn, "sw0879", "bricklink", "new", "market", "ILS",
                            market_price=50.0)
        conn.commit()
        assert unpriced_figs_of(conn, "75192") == []
