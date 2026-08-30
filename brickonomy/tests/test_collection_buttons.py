"""Adding to the collection or wishlist from a set page — offline."""
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


def holding(path, item_id):
    c = dbq.connect(db_path=path)
    try:
        return c.execute("SELECT owned, wanted FROM portfolio WHERE item_id=?",
                         (item_id,)).fetchone()
    finally:
        c.close()


class TestAddToCollection:
    def test_owning_a_set(self, client):
        c, path = client
        r = c.post("/portfolio/add",
                   data={"item_id": "75192", "back": "/sets/75192"},
                   follow_redirects=False)
        assert r.status_code == 303
        assert holding(path, "75192")["owned"] == 1

    def test_it_returns_to_the_page_you_pressed_it_on(self, client):
        """Adding from a set page must not throw you out to the portfolio."""
        c, _ = client
        r = c.post("/portfolio/add",
                   data={"item_id": "75192", "back": "/sets/75192"},
                   follow_redirects=False)
        assert r.headers["location"].startswith("/sets/75192?added=")

    def test_adding_twice_is_two_copies(self, client):
        c, path = client
        for _ in range(2):
            c.post("/portfolio/add", data={"item_id": "75192"},
                   follow_redirects=False)
        assert holding(path, "75192")["owned"] == 2


class TestWishlist:
    def test_wanting_a_set(self, client):
        c, path = client
        c.post("/portfolio/add", data={"item_id": "75192", "want": "true"},
               follow_redirects=False)
        row = holding(path, "75192")
        assert row["wanted"] == 1 and row["owned"] == 0

    def test_wanting_something_owned_leaves_the_holding_alone(self, client):
        """A second copy wanted is not a contradiction, and must not wipe out
        the fact that one is already owned."""
        c, path = client
        c.post("/portfolio/add", data={"item_id": "75192"}, follow_redirects=False)
        c.post("/portfolio/add", data={"item_id": "75192", "want": "true"},
               follow_redirects=False)
        row = holding(path, "75192")
        assert row["owned"] == 1 and row["wanted"] == 1


class TestRedirectIsNotAnOpenDoor:
    @pytest.mark.parametrize("back", [
        "https://evil.example/steal",
        "//evil.example/steal",
    ])
    def test_an_offsite_return_path_is_refused(self, client, back):
        """`back` comes from a form field, so it cannot be trusted to be a
        path on this site."""
        c, _ = client
        r = c.post("/portfolio/add", data={"item_id": "75192", "back": back},
                   follow_redirects=False)
        assert r.headers["location"].startswith("/portfolio?")

    def test_a_local_path_is_honoured(self, client):
        c, _ = client
        r = c.post("/portfolio/add",
                   data={"item_id": "75192", "back": "/sets/75192"},
                   follow_redirects=False)
        assert r.headers["location"].startswith("/sets/75192")


class TestButtonsOnThePage:
    def test_they_say_what_pressing_them_will_do(self, client):
        c, _ = client
        assert "+ I own this" in c.get("/sets/75192").text
        c.post("/portfolio/add", data={"item_id": "75192"}, follow_redirects=False)
        html = c.get("/sets/75192").text
        assert "owned ×1" in html, "an owned set does not offer to be added blindly"

    def test_hidden_in_the_static_export(self, client, monkeypatch):
        """The published site has no server to post to."""
        from brickonomy.web import app as app_mod
        monkeypatch.setattr(app_mod, "STATIC_MODE", True)
        c, _ = client
        assert "/portfolio/add" not in c.get("/sets/75192").text
