"""The compare page and the facts endpoint it renders from.

The page itself is client-rendered, so what is tested here is the contract it
depends on: the route exists, the facts a comparison needs are served, and the
export writes both so the published site can compare anything the local app
can.
"""
import pytest
from starlette.testclient import TestClient

from brickonomy import db as dbq


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from brickonomy.config import get_config
    from brickonomy.currency import reset_rate_cache
    from brickonomy.web import app as app_mod
    reset_rate_cache()
    monkeypatch.setattr(get_config(), "auto_scan_on_view_days", 0)
    path = str(tmp_path / "cmp.db")
    c = dbq.connect(db_path=path)
    for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
        dbq.upsert_rate(c, "USD", q, r)
    dbq.upsert_item(c, "76178", name="Daily Bugle", item_type="S", year=2021,
                    theme="Super Heroes Marvel", parts=3772, minifigs=25,
                    retail_price=349.99, retail_currency="USD")
    dbq.insert_snapshot(c, "76178", "blended", "new", "market", "ILS",
                        market_price=1279.28, confidence="HIGH")
    dbq.insert_snapshot(c, "76178", "blended", "used", "market", "ILS",
                        market_price=988.85)
    dbq.upsert_item(c, "75192", name="Millennium Falcon", item_type="S",
                    year=2017, theme="Star Wars", parts=7541)
    dbq.insert_snapshot(c, "75192", "blended", "new", "market", "ILS",
                        market_price=2089.63)
    c.commit()
    c.close()
    monkeypatch.setattr(app_mod, "get_conn", lambda: dbq.connect(db_path=path))
    return TestClient(app_mod.app)


class TestComparePage:
    def test_it_renders(self, client):
        html = client.get("/compare").text
        assert "cmpTable" in html and "cmpChart" in html

    def test_the_nav_offers_it(self, client):
        assert ">Compare<" in client.get("/").text

    def test_ids_in_the_url_do_not_reach_the_server(self, client):
        """The comparison is chosen on the client; the server renders the same
        shell either way, which is what lets the page be exported once."""
        assert client.get("/compare?ids=76178,75192").status_code == 200


class TestFactsApi:
    def test_it_carries_what_a_comparison_needs(self, client):
        f = client.get("/api/sets/76178/facts").json()
        assert f["name"] == "Daily Bugle"
        assert f["parts"] == 3772 and f["minifigs"] == 25
        assert f["value_new"] == pytest.approx(1279.28)
        assert f["value_used"] == pytest.approx(988.85)
        assert f["ppp"] == pytest.approx(1279.28 / 3772, rel=1e-3)

    def test_retail_keeps_the_currency_lego_priced_it_in(self, client):
        f = client.get("/api/sets/76178/facts").json()
        assert f["retail_native"] == pytest.approx(349.99)
        assert f["retail_ccy"] == "USD"
        assert f["retail"] > f["retail_native"], "converted for comparison too"

    def test_a_missing_number_is_null_not_zero(self, client):
        """A dash and a zero mean different things, and the page marks a
        best-in-row: a zero would win the 'cheapest' row outright."""
        f = client.get("/api/sets/75192/facts").json()
        assert f["retail"] is None
        assert f["value_used"] is None
        assert f["cheapest"] is None

    def test_an_unknown_item_is_a_404_not_an_empty_row(self, client):
        assert client.get("/api/sets/nope-xyz/facts").status_code == 404

    def test_it_follows_the_display_currency(self, client):
        client.cookies.set("ccy", "USD")
        f = client.get("/api/sets/76178/facts").json()
        assert f["currency"] == "USD"
        assert f["value_new"] < 1279.28, "converted out of shekels"


class TestExported:
    def test_the_page_and_the_facts_are_written(self, tmp_path, monkeypatch):
        from brickonomy.config import get_config
        from brickonomy.export import export
        monkeypatch.setattr(get_config(), "auto_scan_on_view_days", 0)
        path = str(tmp_path / "e.db")
        c = dbq.connect(db_path=path)
        for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
            dbq.upsert_rate(c, "USD", q, r)
        dbq.upsert_item(c, "76178", name="Daily Bugle", item_type="S", year=2021)
        dbq.insert_snapshot(c, "76178", "blended", "new", "market", "ILS",
                            market_price=1279.28)
        c.commit()
        c.close()
        monkeypatch.setattr(get_config(), "db_path", path)
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        assert (out / "compare.html").exists()
        assert (out / "api" / "sets" / "76178" / "facts.json").exists()
        assert (out / "api" / "sets" / "76178" / "history.json").exists()
