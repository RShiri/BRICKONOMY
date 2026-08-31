"""Catalog listing filters — offline, on a synthetic catalog."""
import pytest

from brickonomy import db as dbq


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "list.db")
    c = dbq.connect(db_path=path)
    # Ids sort as text, so the Marvel sets (76xxx) land after 600 filler rows.
    # This is the exact shape that made /sets?theme=... show a single set: the
    # route fetched the first N rows by id and filtered afterwards. The filler
    # ids start at 2 so they cannot collide with 10075.
    for i in range(600):
        dbq.upsert_item(c, f"2{i:04d}", name=f"Filler {i}", item_type="S",
                        theme="City")
    dbq.upsert_item(c, "10075", name="Spider-Man Action Pack", item_type="S",
                    theme="Super Heroes Marvel")
    for n in range(76300, 76340):
        dbq.upsert_item(c, str(n), name=f"Marvel {n}", item_type="S",
                        theme="Super Heroes Marvel")
    for i in range(30):
        dbq.upsert_item(c, f"sh{i:04d}", name=f"Fig {i}", item_type="M")
    c.commit()
    c.close()
    return path


@pytest.fixture()
def conn(db_path):
    c = dbq.connect(db_path=db_path)
    yield c
    c.close()


class TestListItems:
    def test_theme_filter_reaches_past_the_row_limit(self, conn):
        """The bug: a themed listing capped at 400 rows by id returned only
        10075, because every other Marvel set sorts after the cap."""
        rows = dbq.list_items(conn, theme="Super Heroes Marvel", limit=400)
        ids = {r["item_id"] for r in rows}
        assert len(rows) == 41, "every Marvel set, not just the low-numbered one"
        assert "10075" in ids
        assert "76339" in ids, "high ids must survive the limit"
        assert all(r["theme"] == "Super Heroes Marvel" for r in rows)

    def test_item_type_filter_separates_sets_from_figs(self, conn):
        figs = dbq.list_items(conn, item_type="M", limit=400)
        assert len(figs) == 30
        assert all(r["item_type"] == "M" for r in figs)

        sets = dbq.list_items(conn, item_type="S", limit=1000)
        assert len(sets) == 641
        assert all(r["item_type"] == "S" for r in sets)

    def test_search_combines_with_theme_instead_of_widening_it(self, conn):
        """The search clause is three ORs; without parentheses an added theme
        filter would bind to the last one only and leak other themes in."""
        rows = dbq.list_items(conn, search="Marvel", theme="Super Heroes Marvel",
                              limit=400)
        assert rows, "search inside a theme still matches"
        assert all(r["theme"] == "Super Heroes Marvel" for r in rows)

        # 'City' matches the filler names' theme via the search LIKE, so this
        # would return rows if the AND/OR grouping were wrong.
        leaked = dbq.list_items(conn, search="City", theme="Super Heroes Marvel",
                                limit=400)
        assert leaked == []

    def test_no_filters_still_returns_the_whole_catalog(self, conn):
        assert len(dbq.list_items(conn, limit=1000)) == 671


class TestListingRoutes:
    def _client(self, db_path, monkeypatch):
        from starlette.testclient import TestClient

        from brickonomy.web import app as app_mod

        # A fresh connection per request: TestClient serves on another thread,
        # and sqlite objects are bound to the thread that created them.
        monkeypatch.setattr(app_mod, "get_conn",
                            lambda: dbq.connect(db_path=db_path))
        return TestClient(app_mod.app)

    def test_marvel_theme_page_lists_the_whole_theme(self, db_path, monkeypatch):
        html = self._client(db_path, monkeypatch).get(
            "/sets?theme=Super+Heroes+Marvel").text
        assert "76339" in html
        assert "Filler" not in html, "other themes must not leak in"

    def test_minifigs_tab_shows_figures_not_sets(self, db_path, monkeypatch):
        html = self._client(db_path, monkeypatch).get("/minifigs").text
        assert "sh0007" in html
        assert "Minifigures" in html
        assert "Filler" not in html, "the sets must not appear on the fig tab"


class TestValueOrdering:
    """Sorting by value has to happen in SQL. Ordering a capped window
    afterwards ranks only what fell inside it — the same trap the theme filter
    fell into, invisible while few items are priced and wrong as soon as the
    scanner outgrows the cap."""

    def _priced(self, conn, item_id, value):
        dbq.insert_snapshot(conn, item_id, "blended", "new", "market", "ILS",
                            market_price=value)
        conn.commit()

    def test_the_most_valuable_item_wins_even_from_outside_the_window(self, conn):
        # 76339 sorts last by id, so a window of 5 by id would never see it.
        self._priced(conn, "20000", 10.0)
        self._priced(conn, "76339", 9999.0)
        rows = dbq.list_items(conn, order="value", limit=5)
        assert rows[0]["item_id"] == "76339"

    def test_it_combines_with_a_theme_filter(self, conn):
        self._priced(conn, "76300", 500.0)
        self._priced(conn, "20001", 9999.0)          # City, must not appear
        rows = dbq.list_items(conn, theme="Super Heroes Marvel",
                              order="value", limit=5)
        assert rows[0]["item_id"] == "76300"
        assert all(r["theme"] == "Super Heroes Marvel" for r in rows)

    def test_it_combines_with_search(self, conn):
        self._priced(conn, "76301", 400.0)
        rows = dbq.list_items(conn, search="Marvel", order="value", limit=5)
        assert rows[0]["item_id"] == "76301"

    def test_it_combines_with_the_item_type(self, conn):
        self._priced(conn, "sh0005", 800.0)
        self._priced(conn, "76302", 9999.0)
        rows = dbq.list_items(conn, item_type="M", order="value", limit=5)
        assert rows[0]["item_id"] == "sh0005"
        assert all(r["item_type"] == "M" for r in rows)

    def test_unpriced_items_sort_last_not_first(self, conn):
        self._priced(conn, "76303", 50.0)
        rows = dbq.list_items(conn, item_type="S", order="value", limit=3)
        assert rows[0]["item_id"] == "76303"

    def test_the_default_order_is_unchanged(self, conn):
        rows = dbq.list_items(conn, item_type="M", limit=5)
        assert [r["item_id"] for r in rows] == sorted(r["item_id"] for r in rows)


class TestLatestValues:
    """One query for every headline value. /themes used to call current_value
    per row, so its cost tracked the catalog (17k sets) rather than the priced
    subset (a few hundred)."""

    def test_it_returns_only_the_freshest_row_per_item(self, conn):
        dbq.insert_snapshot(conn, "10075", "blended", "new", "market", "ILS",
                            market_price=100.0, scraped_at="2026-01-01T00:00:00")
        dbq.insert_snapshot(conn, "10075", "blended", "new", "market", "ILS",
                            market_price=250.0, scraped_at="2026-06-01T00:00:00")
        conn.commit()
        assert dbq.latest_values(conn, "new")["10075"] == 250.0

    def test_it_keeps_the_conditions_apart(self, conn):
        dbq.insert_snapshot(conn, "76300", "blended", "new", "market", "ILS",
                            market_price=400.0)
        dbq.insert_snapshot(conn, "76300", "blended", "used", "market", "ILS",
                            market_price=180.0)
        conn.commit()
        assert dbq.latest_values(conn, "new")["76300"] == 400.0
        assert dbq.latest_values(conn, "used")["76300"] == 180.0

    def test_unpriced_items_are_absent_rather_than_zero(self, conn):
        assert "20000" not in dbq.latest_values(conn, "new")

    def test_it_agrees_with_current_value(self, conn):
        from brickonomy.analytics.valuation import current_value

        dbq.insert_snapshot(conn, "76301", "blended", "new", "market", "ILS",
                            market_price=333.0)
        conn.commit()
        assert dbq.latest_values(conn, "new")["76301"] == current_value(
            conn, "76301", "new")[0]


class TestThemeAsCatalogued:
    """Rebrickable's dump and BrickEconomy's export disagree on case, and
    SQLite groups the two spellings as two themes — so one theme becomes two
    entries on /themes, each with its own averages."""

    def test_a_differently_cased_theme_snaps_to_the_catalog_spelling(self, conn):
        assert dbq.theme_as_catalogued(conn, "super heroes marvel") == \
            "Super Heroes Marvel"
        assert dbq.theme_as_catalogued(conn, "CITY") == "City"

    def test_the_majority_spelling_wins(self, conn):
        # 41 rows say "Super Heroes Marvel"; one stray row disagrees.
        dbq.upsert_item(conn, "76999", name="Stray", item_type="S",
                        theme="SUPER HEROES MARVEL")
        conn.commit()
        assert dbq.theme_as_catalogued(conn, "Super Heroes Marvel") == \
            "Super Heroes Marvel"

    def test_an_unknown_theme_is_left_exactly_as_given(self, conn):
        assert dbq.theme_as_catalogued(conn, "Monkie Kid") == "Monkie Kid"

    def test_no_theme_is_not_an_error(self, conn):
        assert dbq.theme_as_catalogued(conn, None) is None
        assert dbq.theme_as_catalogued(conn, "") == ""


class TestSearchIndex:
    """The header search walks the catalog index without filtering by type,
    so anything in that file is reachable from the search box."""

    def _client(self, db_path, monkeypatch):
        from starlette.testclient import TestClient

        from brickonomy.web import app as app_mod
        monkeypatch.setattr(app_mod, "get_conn",
                            lambda: dbq.connect(db_path=db_path))
        return TestClient(app_mod.app)

    def test_printed_parts_are_not_searchable(self, db_path, monkeypatch):
        c = dbq.connect(db_path=db_path)
        dbq.upsert_item(c, "90398pb007", name="Ant-Man Statuette",
                        item_type="P")
        c.commit()
        c.close()
        data = self._client(db_path, monkeypatch).get("/api/index").json()
        ids = {r[0] for r in data["rows"]}
        assert "90398pb007" not in ids
        assert not [r for r in data["rows"] if r[5] == "P"]

    def test_sets_and_figures_are(self, db_path, monkeypatch):
        data = self._client(db_path, monkeypatch).get("/api/index").json()
        ids = {r[0] for r in data["rows"]}
        assert "10075" in ids and "sh0007" in ids


class TestPagesDoNotWalkTheCatalog:
    """Both /deals and the dashboard queried per item over all 34,880 catalog
    rows to find the few hundred that could appear — 140,000 queries a page
    load, essentially all of them asking about sets never scanned."""

    def _counted(self, db_path, monkeypatch):
        from starlette.testclient import TestClient

        from brickonomy.web import app as app_mod
        seen = []
        real = dbq.connect

        def traced():
            conn = real(db_path=db_path)
            conn.set_trace_callback(lambda _sql: seen.append(1))
            return conn

        monkeypatch.setattr(app_mod, "get_conn", traced)
        return TestClient(app_mod.app), seen

    def test_deals_does_not_query_per_catalog_row(self, db_path, monkeypatch):
        client, seen = self._counted(db_path, monkeypatch)
        client.get("/deals")
        seen.clear()
        client.get("/deals")
        # 671 items in this fixture, none with a listing. Anything close to
        # one query per item means the catalog is being walked again.
        assert len(seen) < 200, f"{len(seen)} queries for 671 items"

    def test_the_dashboard_does_not_either(self, db_path, monkeypatch):
        client, seen = self._counted(db_path, monkeypatch)
        client.get("/")
        seen.clear()
        client.get("/")
        assert len(seen) < 200, f"{len(seen)} queries for 671 items"

    def test_a_deal_still_appears(self, db_path, monkeypatch):
        """The narrower query must not lose one: a deal needs a live listing,
        and a listing lives in a stock snapshot."""
        import json
        c = dbq.connect(db_path=db_path)
        dbq.insert_snapshot(c, "10075", "blended", "new", "market", "ILS",
                            market_price=500.0)
        dbq.insert_snapshot(c, "10075", "bricklink", "new", "stock", "ILS",
                            price_avg=100.0, listing_count=6,
                            raw=[{"price": 100.0, "description": ""}] * 6)
        c.commit()
        c.close()
        client, _ = self._counted(db_path, monkeypatch)
        assert "10075" in client.get("/deals").text
