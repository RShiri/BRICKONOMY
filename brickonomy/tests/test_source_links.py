"""Every price on a set page is scraped from BrickLink, BrickOwl and eBay;
the page should link back to where it read them. Offline."""
import pytest
from starlette.testclient import TestClient

from brickonomy import db as dbq
from brickonomy.web.app import source_url


class TestSourceUrl:
    def test_bricklink_sets_carry_the_catalog_suffix(self):
        assert source_url("bricklink", "76105", "S") == (
            "https://www.bricklink.com/v2/catalog/catalogitem.page?S=76105-1#T=P")

    def test_bricklink_minifigs_use_their_bare_id(self):
        assert source_url("bricklink", "sh0571", "M").endswith("?M=sh0571#T=P")

    def test_type_is_inferred_from_the_id_when_missing(self):
        """Older catalog rows have no item_type; a letter-led id is a fig."""
        assert "?M=sh0571" in source_url("bricklink", "sh0571", None)
        assert "?S=76105-1" in source_url("bricklink", "76105", None)

    def test_an_id_that_already_has_a_suffix_is_left_alone(self):
        assert "?S=76105-1#" in source_url("bricklink", "76105-1", "S")

    def test_other_marketplaces_search_by_number(self):
        assert source_url("brickowl", "76105", "S") == (
            "https://www.brickowl.com/search/catalog?query=76105")
        assert source_url("ebay", "76105", "S") == (
            "https://www.ebay.com/sch/i.html?_nkw=lego+76105&LH_BIN=1")

    def test_unknown_source_is_empty_not_an_error(self):
        assert source_url("brickeconomy", "76105", "S") == ""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from brickonomy.config import get_config
    from brickonomy.currency import reset_rate_cache
    from brickonomy.web import app as app_mod
    reset_rate_cache()
    monkeypatch.setattr(get_config(), "auto_scan_on_view_days", 0)
    path = str(tmp_path / "c.db")
    c = dbq.connect(db_path=path)
    for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
        dbq.upsert_rate(c, "USD", q, r)
    dbq.upsert_item(c, "76105", name="The Hulkbuster: Ultron Edition",
                    theme="Super Heroes Marvel", year=2018, item_type="S")
    dbq.upsert_item(c, "sh0571", name="Black Widow", theme="Super Heroes Marvel",
                    year=2019, item_type="M")
    c.commit()
    c.close()
    monkeypatch.setattr(app_mod, "get_conn", lambda: dbq.connect(db_path=path))
    yield TestClient(app_mod.app)


class TestPagesLinkOut:
    def test_set_page_links_to_all_three_marketplaces(self, client):
        html = client.get("/sets/76105").text
        assert "catalogitem.page?S=76105-1#T=P" in html
        assert "brickowl.com/search/catalog?query=76105" in html
        assert "ebay.com/sch/i.html?_nkw=lego+76105" in html

    def test_links_open_in_a_new_tab_without_leaking_the_opener(self, client):
        html = client.get("/sets/76105").text
        i = html.index("catalogitem.page?S=76105-1")
        tag = html[html.rfind("<a", 0, i):html.index(">", i)]
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag

    def test_minifig_page_links_to_its_bricklink_entry(self, client):
        html = client.get("/sets/sh0571").text
        assert "catalogitem.page?M=sh0571#T=P" in html
        assert "brickowl.com/search/catalog?query=sh0571" in html
