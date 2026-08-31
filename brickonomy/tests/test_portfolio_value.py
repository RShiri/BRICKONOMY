"""What the collection is worth, and when its history is worth plotting.

Two defects lived here undetected because nothing valued a used holding or
looked at the early end of the history series.
"""
import pytest
from starlette.testclient import TestClient

from brickonomy import db as dbq

import re


def headline(html):
    """The Portfolio value tile, not any of the catalog tables around it —
    those legitimately show a set's new price whatever condition it is held
    in."""
    m = re.search(r'Portfolio value</div>\s*<div class="value">([^<]+)', html)
    assert m, "portfolio value tile not found"
    return m.group(1)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from brickonomy.config import get_config
    from brickonomy.currency import reset_rate_cache
    from brickonomy.web import app as app_mod
    reset_rate_cache()
    # A set page queues a real scrape for anything stale; in a test that
    # spawns a scraper thread and never finishes.
    monkeypatch.setattr(get_config(), "auto_scan_on_view_days", 0)
    path = str(tmp_path / "p.db")
    c = dbq.connect(db_path=path)
    for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
        dbq.upsert_rate(c, "USD", q, r)
    c.commit()
    c.close()
    monkeypatch.setattr(app_mod, "get_conn", lambda: dbq.connect(db_path=path))
    return TestClient(app_mod.app), path


def hold(path, item_id, condition, new_value, used_value, when=None):
    c = dbq.connect(db_path=path)
    dbq.upsert_item(c, item_id, name=f"Set {item_id}", item_type="S",
                    theme="Star Wars", year=2020)
    dbq.upsert_portfolio(c, item_id, owned=1, condition=condition)
    for cond, value in (("new", new_value), ("used", used_value)):
        dbq.insert_snapshot(c, item_id, "blended", cond, "market", "ILS",
                            market_price=value,
                            **({"scraped_at": when} if when else {}))
    c.commit()
    c.close()


class TestHeldCondition:
    """The dashboard valued every holding as new. This collection is 76 used
    sets and 16 new ones, so the landing page overstated it by 43% and
    disagreed with the portfolio page, which had it right."""

    def test_a_used_holding_is_valued_used(self, app):
        client, path = app
        hold(path, "75192", "used", new_value=1000.0, used_value=400.0)
        assert "400" in headline(client.get("/").text)
        assert "1,000" not in headline(client.get("/").text),             "the new price must not stand in for it"

    def test_a_new_holding_is_still_valued_new(self, app):
        client, path = app
        hold(path, "75192", "new", new_value=1000.0, used_value=400.0)
        assert "1,000" in headline(client.get("/").text)

    def test_a_mixed_collection_totals_each_row_in_its_own_condition(self, app):
        client, path = app
        hold(path, "75192", "used", new_value=1000.0, used_value=400.0)
        hold(path, "10283", "new", new_value=300.0, used_value=100.0)
        assert "700" in headline(client.get("/").text)   # 400 used + 300 new

    def test_the_dashboard_agrees_with_the_portfolio_page(self, app):
        client, path = app
        hold(path, "75192", "used", new_value=1000.0, used_value=400.0)
        hold(path, "10283", "used", new_value=800.0, used_value=250.0)
        assert "650" in headline(client.get("/").text)
        assert "650" in client.get("/portfolio").text


class TestHistoryCoverage:
    """A set contributes nothing to the total until it has been scanned once,
    so an early date measured how much of the collection had a price rather
    than what it was worth — a line that rose as the scanner caught up."""

    def test_days_before_the_collection_is_covered_are_not_plotted(self, app):
        client, path = app
        # 75192 was scanned in January; 10283 only in August. A January point
        # would show the portfolio at 400 and imply it tripled since.
        hold(path, "75192", "used", 1000.0, 400.0, when="2026-01-05T00:00:00")
        hold(path, "10283", "used", 2000.0, 800.0, when="2026-08-05T00:00:00")
        hold(path, "75192", "used", 1000.0, 400.0, when="2026-08-05T00:00:00")

        data = client.get("/api/portfolio/history").json()
        assert data["tracked"] == 2
        assert [p["t"] for p in data["series"]] == ["2026-08-05"]
        assert data["series"][0]["v"] == pytest.approx(1200.0)

    def test_the_last_point_equals_what_the_portfolio_is_worth_today(self, app):
        client, path = app
        hold(path, "75192", "used", 1000.0, 400.0)
        hold(path, "10283", "new", 300.0, 100.0)
        series = client.get("/api/portfolio/history").json()["series"]
        assert series[-1]["v"] == pytest.approx(700.0)

    def test_a_fully_covered_history_keeps_every_day(self, app):
        client, path = app
        for when in ("2026-01-05T00:00:00", "2026-04-05T00:00:00",
                     "2026-08-05T00:00:00"):
            hold(path, "75192", "used", 1000.0, 400.0, when=when)
        series = client.get("/api/portfolio/history").json()["series"]
        assert len(series) == 3, "one set, always covered — nothing to trim"

    def test_an_empty_collection_is_not_an_error(self, app):
        client, _ = app
        data = client.get("/api/portfolio/history").json()
        assert data["series"] == [] and data["covered_from"] is None


def gain(html):
    m = re.search(r'Gain vs paid</div>\s*<div class="value">([^<]+)', html)
    assert m, "gain tile not found"
    return m.group(1)


class TestGainBasis:
    """The tile divided the value of every owned set by the retail of the
    subset that had one — 92 sets over 78 — which is a return on nothing. It
    also read "paid/retail" while only ever using retail, so a set bought
    below list showed as a loss."""

    def _own(self, path, item_id, *, value, paid=None, retail=None):
        c = dbq.connect(db_path=path)
        dbq.upsert_item(c, item_id, name=f"Set {item_id}", item_type="S",
                        year=2020, retail_price=retail, retail_currency="ILS")
        dbq.upsert_portfolio(c, item_id, owned=1, condition="used",
                             purchase_price=paid, purchase_currency="ILS")
        dbq.insert_snapshot(c, item_id, "blended", "used", "market", "ILS",
                            market_price=value)
        c.commit()
        c.close()

    def test_what_was_paid_beats_retail_when_both_are_known(self, app):
        client, path = app
        # Bought at 60 in a sale, list price 100, worth 90 now: a gain.
        self._own(path, "75192", value=90.0, paid=60.0, retail=100.0)
        assert gain(client.get("/").text) == "+50.0%"

    def test_retail_stands_in_when_nothing_was_paid(self, app):
        client, path = app
        self._own(path, "75192", value=120.0, retail=100.0)
        assert gain(client.get("/").text) == "+20.0%"

    def test_a_set_with_no_cost_basis_is_left_out_of_both_sides(self, app):
        client, path = app
        self._own(path, "75192", value=120.0, paid=100.0)
        self._own(path, "10283", value=500.0)      # no paid, no retail
        # 500 must not land on the value side while contributing no cost.
        assert gain(client.get("/").text) == "+20.0%"
        assert "1 of 2 sets" in client.get("/").text

    def test_the_count_is_hidden_when_every_set_is_covered(self, app):
        client, path = app
        self._own(path, "75192", value=120.0, paid=100.0)
        html = client.get("/").text
        assert gain(html) == "+20.0%"
        assert "of 1 sets" not in html

    def test_no_collection_is_not_a_division_by_zero(self, app):
        client, _ = app
        assert gain(client.get("/").text) == "—"


class TestDeltaMatchesTheCondition:
    """The 30-day change sat beside the value in the same row, but defaulted
    to the sealed series while the value used the held one — two different
    price series presented as one holding."""

    def _own(self, path, item_id, condition):
        c = dbq.connect(db_path=path)
        dbq.upsert_item(c, item_id, name="Set", item_type="S", year=2016)
        dbq.upsert_portfolio(c, item_id, owned=1, condition=condition)
        # New rises 100 -> 200; used falls 100 -> 50. Nothing but the
        # condition can tell the two apart.
        for cond, then, now in (("new", 100.0, 200.0), ("used", 100.0, 50.0)):
            dbq.insert_snapshot(c, item_id, "blended", cond, "market", "ILS",
                                market_price=then,
                                scraped_at="2026-01-01T00:00:00")
            dbq.insert_snapshot(c, item_id, "blended", cond, "market", "ILS",
                                market_price=now,
                                scraped_at="2026-08-30T00:00:00")
        c.commit()
        c.close()

    def test_a_used_holding_shows_the_used_change(self, app):
        client, path = app
        self._own(path, "75192", "used")
        html = client.get("/portfolio").text
        assert "50.0%" in html, "the used series fell by half"
        assert "100.0%" not in html, "not the sealed series doubling"

    def test_a_new_holding_shows_the_new_change(self, app):
        client, path = app
        self._own(path, "75192", "new")
        assert "100.0%" in client.get("/portfolio").text

    def test_the_dashboard_movers_agree(self, app):
        client, path = app
        self._own(path, "75192", "used")
        # Only the movers tables, not the whole page: a theme bar's width is
        # written as style="width:100.0%" and is not a price change. A halving
        # is a decline, so it belongs to the second of the two.
        html = client.get("/").text
        movers = html[html.index("Top gainers"):html.index("Best deals right now")]
        assert "50.0%" in movers
        assert "100.0%" not in movers, "not the sealed series doubling"
