"""Static export tests — seeded tmp DB, offline."""
import json
from datetime import datetime, timedelta

import pytest

from brickonomy import db as dbq
from brickonomy.config import get_config
from brickonomy.export import export


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    from brickonomy.currency import reset_rate_cache
    reset_rate_cache()
    db_path = tmp_path / "exp.db"
    monkeypatch.setattr(get_config(), "db_path", str(db_path))
    conn = dbq.connect(db_path=str(db_path))
    dbq.upsert_rate(conn, "USD", "ILS", 3.5)
    dbq.upsert_rate(conn, "USD", "EUR", 0.9)
    dbq.upsert_rate(conn, "USD", "GBP", 0.8)

    dbq.upsert_item(conn, "75192", name="Millennium Falcon", theme="Star Wars",
                    year=2017, retail_price=849.99, retail_currency="USD")
    dbq.upsert_item(conn, "sw0879", name="Han Solo", item_type="M", theme="Star Wars")
    dbq.upsert_set_minifigs(conn, "75192", [{"id": "sw0879", "name": "Han Solo", "qty": 1}])
    dbq.upsert_portfolio(conn, "75192", owned=1, purchase_price=849.99)

    for days_ago, price in ((30, 3000.0), (0, 3200.0)):
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        dbq.insert_snapshot(conn, "75192", "blended", "new", "market", "ILS",
                            market_price=price, confidence="HIGH", scraped_at=ts)
    conn.commit()
    conn.close()
    yield db_path


class TestExport:
    def test_export_writes_expected_files(self, seeded_db, tmp_path):
        out = tmp_path / "site"
        n_pages, n_json = export(str(out), "ILS", quiet=True)
        assert (out / "index.html").exists()
        assert (out / "sets" / "index.html").exists()
        assert (out / "sets" / "75192.html").exists()
        # Minifigs of exported sets get pages too (linked from the set page).
        assert (out / "sets" / "sw0879.html").exists()
        assert (out / "set.html").exists()                   # catalog fallback
        assert (out / "sets" / "theme-star-wars.html").exists()
        # The portfolio page exists so the nav link is never dead, but with no
        # password configured it carries no holdings — see test_lockbox.py.
        assert (out / "portfolio.html").exists()
        assert (out / "themes.html").exists()
        assert (out / "deals.html").exists()
        # The nav links to /minifigs from every page, so it must be written or
        # each exported page carries a dead link.
        assert (out / "minifigs.html").exists()
        assert (out / "api" / "index.json").exists()   # header search
        assert (out / "api" / "sets" / "75192" / "history.json").exists()
        assert (out / "static" / "style.css").exists()
        assert (out / ".nojekyll").exists()
        assert n_pages >= 5 and n_json >= 1

    def test_links_are_relative_to_each_page(self, seeded_db, tmp_path):
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        # Root page: links are relative with no prefix.
        html = (out / "index.html").read_text(encoding="utf-8")
        assert 'href="sets/index.html"' in html
        assert 'href="static/style.css"' in html
        assert 'href="/sets"' not in html
        assert 'data-base=""' in html and 'data-static="1"' in html

        # One level down: links climb back out with ../
        sub = (out / "sets" / "75192.html").read_text(encoding="utf-8")
        assert 'href="../index.html"' in sub
        assert 'href="../static/style.css"' in sub
        assert 'data-base="../"' in sub

    def test_server_only_ui_hidden(self, seeded_db, tmp_path, monkeypatch):
        from brickonomy import lockbox
        # With a password the portfolio ships too, so it is covered here.
        monkeypatch.setenv(lockbox.ENV_VAR, "pw")
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        for page in ("index.html", "portfolio.html", "sets/index.html"):
            html = (out / page).read_text(encoding="utf-8")
            assert "Refresh &amp; Sources" not in html, page
            if page != "portfolio.html":
                assert 'method="post"' not in html, page
        locked = (out / "portfolio.html").read_text(encoding="utf-8")
        # The unlock form is the one form allowed; it posts nowhere.
        assert "Preview import" not in locked
        assert 'action=' not in locked

    def test_history_json_parses(self, seeded_db, tmp_path):
        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        data = json.loads((out / "api" / "sets" / "75192" / "history.json").read_text(encoding="utf-8"))
        assert data["currency"] == "ILS"
        assert len(data["series"]["blended"]) == 2

    def test_catalog_only_items_link_to_the_fallback_page(self, seeded_db, tmp_path):
        """A set with no prices gets no page, so links must point at set.html."""
        conn = dbq.connect(db_path=str(seeded_db))
        dbq.upsert_item(conn, "99999", name="Unscanned Set", theme="Star Wars",
                        year=2024)
        conn.close()

        out = tmp_path / "site"
        export(str(out), "ILS", quiet=True)
        assert not (out / "sets" / "99999.html").exists()

        index = json.loads((out / "api" / "index.json").read_text(encoding="utf-8"))
        by_id = {r[0]: r for r in index["rows"]}
        assert by_id["99999"][-1] == 0       # not priced -> client-rendered page
        assert by_id["75192"][-1] == 1

    def test_dynamic_mode_restored_after_export(self, seeded_db, tmp_path):
        from brickonomy.web import app as webapp
        export(str(tmp_path / "site"), "ILS", quiet=True)
        assert webapp.STATIC_MODE is False
        assert webapp.STATIC_PAGED_IDS is None
        assert webapp.static_url("/sets/75192") == "/sets/75192"
