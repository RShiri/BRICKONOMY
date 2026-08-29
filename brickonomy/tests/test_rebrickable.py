"""Rebrickable catalog import — runs against gzipped CSV fixtures, offline."""
from pathlib import Path

import pytest

from brickonomy import db as dbq
from brickonomy.rebrickable import fetch_csv, import_catalog, theme_lookup

FIXTURES = Path(__file__).parent / "fixtures" / "rebrickable"


@pytest.fixture()
def conn(tmp_path):
    from brickonomy.currency import reset_rate_cache
    reset_rate_cache()
    c = dbq.connect(db_path=str(tmp_path / "cat.db"))
    yield c
    c.close()


class TestThemeLookup:
    def test_resolves_parent_chain(self):
        themes = theme_lookup(fetch_csv("themes", FIXTURES))
        assert themes["209"] == ("Star Wars", "Star Wars / Ultimate Collector Series")
        assert themes["158"] == ("Star Wars", "Star Wars")
        assert themes["52"] == ("Technic", "Technic")


class TestImportCatalog:
    def test_imports_sets_with_theme_and_subtheme(self, conn):
        counts = import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)
        assert counts["sets"] == 3      # book + key chain skipped

        falcon = dbq.get_item(conn, "75192")
        assert falcon["name"] == "Millennium Falcon"
        assert falcon["theme"] == "Star Wars"
        assert falcon["subtheme"] == "Ultimate Collector Series"
        assert falcon["year"] == 2017
        assert falcon["parts"] == 7541
        assert falcon["item_type"] == "S"

        # Top-level theme sets have no subtheme.
        assert dbq.get_item(conn, "10188")["subtheme"] is None

    def test_strips_the_rebrickable_suffix(self, conn):
        import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)
        assert dbq.get_item(conn, "75192") is not None
        assert dbq.get_item(conn, "75192-1") is None

    def test_skips_books_and_gear(self, conn):
        import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)
        assert dbq.get_item(conn, "5006061") is None   # Books theme
        assert dbq.get_item(conn, "KEYCH01") is None   # non-numeric id

    def test_minifigs_optional(self, conn):
        counts = import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)
        assert counts["minifigs"] == 0
        assert dbq.get_item(conn, "fig-000123") is None

        counts = import_catalog(conn, from_dir=FIXTURES, with_minifigs=True,
                                log=lambda *a: None)
        assert counts["minifigs"] == 2
        assert dbq.get_item(conn, "fig-000123")["item_type"] == "M"

    def test_rerun_is_idempotent_and_keeps_scraped_data(self, conn):
        import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)
        # A scan later fills in details the catalog dump doesn't carry.
        dbq.upsert_item(conn, "75192", weight_g=13246.0, retail_price=849.99)
        import_catalog(conn, from_dir=FIXTURES, log=lambda *a: None)

        row = dbq.get_item(conn, "75192")
        assert row["weight_g"] == 13246.0        # survived the re-import
        assert row["retail_price"] == 849.99
        assert conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 3

    def test_reads_gzipped_dumps(self, conn):
        # The CDN serves .csv.gz; the fixture dir has both forms.
        rows = fetch_csv("sets", FIXTURES)
        assert any(r["set_num"] == "75192-1" for r in rows)
