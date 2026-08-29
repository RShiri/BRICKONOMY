"""Catalog sync (category tree + set lists) on fixtures — offline."""
from pathlib import Path

import pytest

from brickonomy import db as dbq
from brickonomy.catalog import _theme_from_path, sync_category, sync_tree
from brickonomy.scrapers.bricklink import BrickLinkSource

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureSource(BrickLinkSource):
    """BrickLinkSource whose network fetches read the saved fixture pages."""

    def fetch_category_tree(self, item_type="S"):
        html = (FIXTURES / "bricklink_tree_S.html").read_text()
        return self.parse_category_tree(html), None

    def fetch_catalog_page(self, cat_id, page=1, item_type="S"):
        if cat_id != 66 or page != 1:
            return [], 1, None
        html = (FIXTURES / "bricklink_list_66.html").read_text()
        sets, pages = self.parse_catalog_list(html)
        return sets, 1, None  # pretend single page so the test stays offline


@pytest.fixture()
def conn(tmp_path):
    c = dbq.connect(db_path=str(tmp_path / "cat.db"))
    yield c
    c.close()


class TestSyncTree:
    def test_parent_links_and_paths(self, conn):
        n = sync_tree(conn, FixtureSource(), log=lambda *a: None)
        assert n == 7
        cats = {c["cat_id"]: c for c in dbq.get_categories(conn)}
        assert cats[66]["parent_id"] == 65
        assert cats[66]["path"] == "Star Wars / Ultimate Collector Series"
        assert cats[68]["parent_id"] == 67
        assert cats[68]["path"] == "Star Wars / Episode 4/5/6 / Death Star"
        assert cats[51]["parent_id"] is None

    def test_subtree_ids(self, conn):
        sync_tree(conn, FixtureSource(), log=lambda *a: None)
        ids = set(dbq.category_subtree_ids(conn, 65))
        assert ids == {65, 66, 67, 68}


class TestSyncCategory:
    def test_sets_imported_with_theme_from_path(self, conn):
        src = FixtureSource()
        sync_tree(conn, src, log=lambda *a: None)
        n = sync_category(conn, src, 66, log=lambda *a: None)
        assert n == 3
        row = dbq.get_item(conn, "75192")
        assert row["theme"] == "Star Wars"
        assert row["subtheme"] == "Ultimate Collector Series"
        assert row["category_id"] == 66
        assert row["year"] == 2017
        assert row["name"].startswith("Millennium Falcon")

    def test_theme_from_path(self):
        assert _theme_from_path("Star Wars / UCS") == ("Star Wars", "UCS")
        assert _theme_from_path("Technic") == ("Technic", None)
        assert _theme_from_path(None) == (None, None)


class TestSetMinifigs:
    def test_upsert_and_reverse_lookup(self, conn):
        dbq.upsert_item(conn, "76031", name="Hulk Buster Smash", item_type="S")
        figs = BrickLinkSource().parse_minifig_inventory(
            (FIXTURES / "bricklink_inv_figs_76031.html").read_text())
        dbq.upsert_set_minifigs(conn, "76031", figs)

        stored = dbq.get_set_minifigs(conn, "76031")
        assert len(stored) == 3
        assert {r["fig_id"]: r["qty"] for r in stored}["sh0173"] == 2

        appears = dbq.sets_containing_fig(conn, "sh0173")
        assert len(appears) == 1
        assert appears[0]["set_id"] == "76031"
        assert appears[0]["name"] == "Hulk Buster Smash"

    def test_reupsert_keeps_name_when_blank(self, conn):
        dbq.upsert_item(conn, "111", name="X", item_type="S")
        dbq.upsert_set_minifigs(conn, "111", [{"id": "sw001", "name": "Luke", "qty": 1}])
        dbq.upsert_set_minifigs(conn, "111", [{"id": "sw001", "name": "", "qty": 2}])
        row = dbq.get_set_minifigs(conn, "111")[0]
        assert row["fig_name"] == "Luke"
        assert row["qty"] == 2
