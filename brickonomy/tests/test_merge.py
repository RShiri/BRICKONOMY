"""Merging a database scanned on a runner into the local one — offline.

The merge writes into the database holding the portfolio, so the properties
below are the ones that matter: nothing is lost, nothing is duplicated, and
nothing about the collection is touched.
"""
import pytest

from brickonomy import db as dbq
from brickonomy.merge import merge


def seed_local(path):
    c = dbq.connect(db_path=str(path))
    dbq.upsert_item(c, "75192", name="Millennium Falcon", theme="Star Wars",
                    year=2017, item_type="S")
    dbq.upsert_portfolio(c, "75192", owned=1, purchase_price=849.99,
                         purchase_currency="USD", condition="used")
    dbq.insert_snapshot(c, "75192", "bricklink", "new", "market", "ILS",
                        market_price=3000.0, scraped_at="2026-08-01T10:00:00")
    dbq.upsert_set_minifigs(c, "75192", [{"id": "sw0879", "name": "Han Solo", "qty": 1}])
    dbq.upsert_rate(c, "USD", "ILS", 3.5)
    c.commit()
    return c


def seed_incoming(path):
    c = dbq.connect(db_path=str(path))
    # Same snapshot the local db already has, plus two it does not.
    dbq.insert_snapshot(c, "75192", "bricklink", "new", "market", "ILS",
                        market_price=3000.0, scraped_at="2026-08-01T10:00:00")
    dbq.insert_snapshot(c, "75192", "bricklink", "new", "market", "ILS",
                        market_price=3200.0, scraped_at="2026-08-20T10:00:00")
    dbq.insert_snapshot(c, "75300", "bricklink", "new", "market", "ILS",
                        market_price=140.0, scraped_at="2026-08-20T10:05:00")
    # A set the local db has never heard of, and a stale name for one it has.
    dbq.upsert_item(c, "75300", name="TIE Fighter", theme="Star Wars",
                    year=2021, parts=432, item_type="S")
    dbq.upsert_item(c, "75192", name="WRONG NAME", theme="Star Wars", parts=7541)
    dbq.upsert_set_minifigs(c, "75300", [{"id": "sw1234", "name": "Pilot", "qty": 2}])
    dbq.upsert_set_minifigs(c, "75192", [{"id": "WRONG", "name": "bad parse", "qty": 9}])
    dbq.upsert_part_out(c, "75300", 210.0, "USD")
    # The runner seeds its portfolio from the repo CSV — not authoritative.
    dbq.upsert_portfolio(c, "99999", owned=5, purchase_price=1.0)
    dbq.upsert_rate(c, "USD", "ILS", 9.99)
    c.commit()
    c.close()
    return path


@pytest.fixture()
def dbs(tmp_path):
    local = tmp_path / "local.db"
    incoming = tmp_path / "incoming.db"
    conn = seed_local(local)
    seed_incoming(incoming)
    yield conn, incoming
    conn.close()


class TestMerge:
    def test_new_snapshots_arrive_and_duplicates_do_not(self, dbs):
        conn, incoming = dbs
        before = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        out = merge(conn, incoming, log=lambda *a: None)
        after = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        assert out["snapshots"] == 2, "the two genuinely new rows"
        assert after == before + 2, "the shared row was not duplicated"

    def test_merging_twice_changes_nothing_the_second_time(self, dbs):
        """Re-running after a failed download must be safe."""
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        mid = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        second = merge(conn, incoming, log=lambda *a: None)
        end = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        assert second["snapshots"] == 0 and end == mid

    def test_nothing_local_is_lost(self, dbs):
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        row = conn.execute(
            """SELECT * FROM price_snapshots
               WHERE item_id='75192' AND scraped_at='2026-08-01T10:00:00'""").fetchone()
        assert row["market_price"] == pytest.approx(3000.0)

    def test_the_portfolio_is_never_touched(self, dbs):
        """The runner's portfolio is seeded from the repo CSV; ours holds what
        was actually paid."""
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        rows = {r["item_id"]: r for r in dbq.get_portfolio(conn)}
        assert set(rows) == {"75192"}, "no holding may arrive from a runner"
        assert rows["75192"]["purchase_price"] == pytest.approx(849.99)
        assert rows["75192"]["condition"] == "used"

    def test_exchange_rates_are_never_touched(self, dbs):
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        rate = conn.execute(
            "SELECT rate FROM exchange_rates WHERE base='USD' AND quote='ILS'"
        ).fetchone()["rate"]
        assert rate == pytest.approx(3.5), "local rates stay authoritative"

    def test_known_metadata_wins_over_the_incoming_copy(self, dbs):
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        item = dbq.get_item(conn, "75192")
        assert item["name"] == "Millennium Falcon", "a known name is not churned"
        assert item["parts"] == 7541, "but a hole is filled"

    def test_unknown_items_are_added(self, dbs):
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        item = dbq.get_item(conn, "75300")
        assert item is not None and item["name"] == "TIE Fighter"

    def test_inventories_arrive_only_for_sets_that_have_none(self, dbs):
        """A locally repaired inventory must not be replaced by a runner's
        older parse — that is how the 90398pb007 corruption would return."""
        conn, incoming = dbs
        merge(conn, incoming, log=lambda *a: None)
        local_figs = [r["fig_id"] for r in dbq.get_set_minifigs(conn, "75192")]
        assert local_figs == ["sw0879"], "our inventory stands"
        assert [r["fig_id"] for r in dbq.get_set_minifigs(conn, "75300")] == ["sw1234"]

    def test_dry_run_writes_nothing(self, dbs):
        conn, incoming = dbs
        before = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        out = merge(conn, incoming, dry_run=True, log=lambda *a: None)
        after = conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
        assert after == before
        assert out["would_add_snapshots"] == 2 and out["would_add_items"] == 1

    def test_a_missing_file_is_an_error_not_a_silent_no_op(self, dbs, tmp_path):
        conn, _ = dbs
        with pytest.raises(FileNotFoundError):
            merge(conn, tmp_path / "nope.db", log=lambda *a: None)

    def test_the_incoming_database_is_not_modified(self, dbs):
        """It is attached read-only; a merge must never write back."""
        conn, incoming = dbs
        before = incoming.stat().st_size
        merge(conn, incoming, log=lambda *a: None)
        other = dbq.connect(db_path=str(incoming))
        try:
            assert other.execute(
                "SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"] == 3
        finally:
            other.close()
        assert incoming.stat().st_size == before


class TestThemeRanking:
    def test_owned_themes_outrank_bigger_unowned_ones(self, tmp_path):
        """Runner time is finite; a theme already collected earns its scan
        before a larger one nobody here owns."""
        from brickonomy.merge import rank_themes
        c = dbq.connect(db_path=str(tmp_path / "r.db"))
        try:
            for n in range(5):                       # small, but collected
                dbq.upsert_item(c, f"1000{n}", name=f"Owned {n}",
                                theme="Star Wars", year=2015, item_type="S")
            dbq.upsert_portfolio(c, "10000", owned=1)
            for n in range(40):                      # large, but not collected
                dbq.upsert_item(c, f"2000{n}", name=f"Other {n}",
                                theme="Duplo", year=2015, item_type="S")
            c.commit()
            ranked = [t["theme"] for t in rank_themes(c)]
            assert ranked[0] == "Star Wars"
        finally:
            c.close()

    def test_fully_scanned_themes_drop_off_the_list(self, tmp_path):
        from brickonomy.merge import rank_themes
        c = dbq.connect(db_path=str(tmp_path / "r2.db"))
        try:
            dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars",
                            year=2017, item_type="S")
            dbq.insert_snapshot(c, "75192", "bricklink", "new", "market", "ILS",
                                market_price=100.0)
            c.commit()
            assert rank_themes(c) == [], "nothing left to scan there"
        finally:
            c.close()

    def test_minifigs_do_not_inflate_the_set_counts(self, tmp_path):
        from brickonomy.merge import rank_themes
        c = dbq.connect(db_path=str(tmp_path / "r3.db"))
        try:
            dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars",
                            year=2017, item_type="S")
            dbq.upsert_item(c, "sw0879", name="Han", theme="Star Wars",
                            year=2017, item_type="M")
            c.commit()
            assert rank_themes(c)[0]["total"] == 1
        finally:
            c.close()
