"""The phone bottom dock: five tabs on every page, the right one active, and
no /refresh link once the site is a static export (mirrors the client
fixture in test_collection_buttons.py)."""
import re

import pytest
from starlette.testclient import TestClient

from brickonomy import db as dbq


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from brickonomy.config import get_config
    from brickonomy.currency import reset_rate_cache
    from brickonomy.web import app as app_mod
    reset_rate_cache()
    # Opening a set page queues a real scrape for anything stale. In a test
    # that spawns a scraper thread and the run never finishes.
    monkeypatch.setattr(get_config(), "auto_scan_on_view_days", 0)
    path = str(tmp_path / "c.db")
    c = dbq.connect(db_path=path)
    for q, r in (("ILS", 3.5), ("EUR", 0.9), ("GBP", 0.8)):
        dbq.upsert_rate(c, "USD", q, r)
    dbq.upsert_item(c, "75192", name="Falcon", theme="Star Wars", year=2017,
                    item_type="S")
    c.commit()
    c.close()
    monkeypatch.setattr(app_mod, "get_conn", lambda: dbq.connect(db_path=path))
    yield TestClient(app_mod.app), path


def dock_tabs(html):
    return re.findall(r'class="dock-tab[^"]*"', html)


class TestDockRendersEverywhere:
    """Every page carries the same five-tab dock, whatever it renders inside
    <main> — the dock lives in base.html, not per-page markup."""

    @pytest.mark.parametrize("path", [
        "/", "/sets", "/minifigs", "/deals", "/portfolio",
        "/themes", "/compare", "/partout", "/sets/75192",
    ])
    def test_five_tabs_on_every_page(self, client, path):
        c, _ = client
        html = c.get(path).text
        tabs = dock_tabs(html)
        assert len(tabs) == 5, f"{path}: expected 5 .dock-tab elements, found {len(tabs)}"

    def test_the_fifth_tab_is_the_more_button(self, client):
        c, _ = client
        html = c.get("/").text
        assert '<button class="dock-tab dock-more' in html

    def test_the_more_panel_lists_the_overflow_routes(self, client):
        c, _ = client
        html = c.get("/").text
        for label in ("Minifigs", "Themes", "Compare", "Part-out", "Refresh &amp; Sources"):
            assert label in html


class TestActiveTabMatchesPath:
    @pytest.mark.parametrize("path,active_label", [
        ("/", "Dashboard"),
        ("/sets", "Sets"),
        ("/sets/75192", "Sets"),
        ("/deals", "Deals"),
        ("/portfolio", "Portfolio"),
    ])
    def test_the_primary_tab_lights_up(self, client, path, active_label):
        c, _ = client
        html = c.get(path).text
        # The active tab's own <a>/<button> carries both dock-tab and active;
        # its label text should be the one this path is expected to light.
        m = re.search(r'class="dock-tab[^"]*\bactive\b[^"]*"[^>]*>.*?<span class="dock-lbl">([^<]+)</span>',
                       html, re.S)
        assert m, f"{path}: no active dock tab found"
        assert m.group(1) == active_label

    @pytest.mark.parametrize("path", ["/minifigs", "/themes", "/compare", "/partout"])
    def test_more_lights_up_for_its_overflow_routes(self, client, path):
        c, _ = client
        html = c.get(path).text
        m = re.search(r'class="dock-tab[^"]*\bactive\b[^"]*"[^>]*>.*?<span class="dock-lbl">([^<]+)</span>',
                       html, re.S)
        assert m, f"{path}: no active dock tab found"
        assert m.group(1) == "More"


class TestRefreshHiddenInStaticMode:
    """The published site has no server to post a scan to — mirrors
    TestButtonsOnThePage.test_hidden_in_the_static_export in
    test_collection_buttons.py."""

    def test_refresh_link_absent_from_the_dock(self, client, monkeypatch):
        from brickonomy.web import app as app_mod
        monkeypatch.setattr(app_mod, "STATIC_MODE", True)
        c, _ = client
        html = c.get("/").text
        assert 'href="/refresh"' not in html
        assert "Refresh &amp; Sources" not in html
        # The rest of the dock still renders normally.
        assert len(dock_tabs(html)) == 5

    def test_refresh_link_present_off_static_mode(self, client):
        c, _ = client
        html = c.get("/").text
        assert 'href="/refresh"' in html
