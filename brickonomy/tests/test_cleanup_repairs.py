"""Repairs that run over the whole database — so a wrong rule here is not a
wrong screen, it is data destroyed."""
import pytest

from brickonomy import db as dbq
from brickonomy.cleanup import (backfill_minifig_years, derivable_minifig_years,
                                find_bad_names)


@pytest.fixture()
def conn(tmp_path):
    c = dbq.connect(db_path=str(tmp_path / "c.db"))
    yield c
    c.close()


class TestNamesThatAreReallyIds:
    """The rule was "a name with no spaces is an id", on the reasoning that a
    real name is prose. Fifteen rows in this database matched and every one
    was a genuine one-word character name — Sersi, Gambit, Rogue, Spider-Ham.
    Clearing them is not recoverable by an ordinary rescan."""

    def _fig(self, conn, set_id, fig_id, name):
        dbq.upsert_set_minifigs(conn, set_id, [{"id": fig_id, "name": name,
                                                "qty": 1}])
        conn.commit()

    def test_a_one_word_character_name_is_kept(self, conn):
        for fig_id, name in (("sh0765", "Sersi"), ("sh0994", "Gambit"),
                             ("sh0942", "Rogue"), ("sh0638", "Spider-Ham"),
                             ("sh0713", "Blade"), ("sh0269", "Scorpion")):
            self._fig(conn, "76156", fig_id, name)
        assert find_bad_names(conn) == []

    def test_an_id_stored_as_a_name_is_still_caught(self, conn):
        # The original bug: 76051's fig row took the Ant-Man statuette's part
        # number as sh0255's name.
        self._fig(conn, "76051", "sh0255", "90398pb007")
        assert [r["fig_id"] for r in find_bad_names(conn)] == ["sh0255"]

    def test_another_figures_id_is_caught_too(self, conn):
        self._fig(conn, "76051", "sh0254", "sh0177")
        assert [r["fig_id"] for r in find_bad_names(conn)] == ["sh0254"]

    def test_a_name_containing_a_number_is_not_an_id(self, conn):
        # "Iron Man - Mark 46 Armor" has digits, but it also has spaces.
        self._fig(conn, "76051", "sh0254", "Iron Man - Mark 46 Armor")
        assert find_bad_names(conn) == []


class TestMinifigYears:
    """Rebrickable carries no year for most figures, and the year drives the
    lifecycle phase, the growth estimate and the retirement window. A figure's
    debut is the release year of the earliest set it appears in."""

    def _seed(self, conn, fig_id, sets, fig_year=None):
        dbq.upsert_item(conn, fig_id, name="Fig", item_type="M", year=fig_year)
        for set_id, year in sets:
            dbq.upsert_item(conn, set_id, name=f"Set {set_id}", item_type="S",
                            year=year)
            dbq.upsert_set_minifigs(conn, set_id,
                                    [{"id": fig_id, "name": "Fig", "qty": 1}])
        conn.commit()

    def test_a_figure_takes_the_year_of_its_earliest_set(self, conn):
        self._seed(conn, "sh0177", [("76051", 2016), ("76049", 2015)])
        backfill_minifig_years(conn, log=lambda *a: None)
        assert dbq.get_item(conn, "sh0177")["year"] == 2015

    def test_a_figure_that_already_has_a_year_is_left_alone(self, conn):
        self._seed(conn, "sh0177", [("76051", 2016)], fig_year=2014)
        backfill_minifig_years(conn, log=lambda *a: None)
        assert dbq.get_item(conn, "sh0177")["year"] == 2014

    def test_a_set_with_no_year_of_its_own_dates_nothing(self, conn):
        self._seed(conn, "sh0177", [("76051", None)])
        assert derivable_minifig_years(conn) == []
        backfill_minifig_years(conn, log=lambda *a: None)
        assert not dbq.get_item(conn, "sh0177")["year"]

    def test_it_is_idempotent(self, conn):
        self._seed(conn, "sh0177", [("76051", 2016)])
        assert backfill_minifig_years(conn, log=lambda *a: None) == 1
        assert backfill_minifig_years(conn, log=lambda *a: None) == 0

    def test_a_dry_run_writes_nothing(self, conn):
        self._seed(conn, "sh0177", [("76051", 2016)])
        backfill_minifig_years(conn, dry_run=True, log=lambda *a: None)
        assert not dbq.get_item(conn, "sh0177")["year"]
