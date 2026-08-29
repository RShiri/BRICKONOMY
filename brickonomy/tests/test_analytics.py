"""Analytics tests on synthetic snapshot series — fully offline."""
from datetime import datetime, timedelta

import pytest

from brickonomy import db as dbq
from brickonomy.analytics import forecast as forecast_mod
from brickonomy.analytics import growth as growth_mod
from brickonomy.analytics import lifecycle
from brickonomy.analytics.valuation import blend, headline_value, store_blended


@pytest.fixture()
def conn(tmp_path):
    from brickonomy.currency import reset_rate_cache
    reset_rate_cache()
    c = dbq.connect(db_path=str(tmp_path / "test.db"))
    # Pin exchange rates so conversions are deterministic and offline.
    dbq.upsert_rate(c, "USD", "ILS", 3.5)
    dbq.upsert_rate(c, "USD", "EUR", 0.9)
    dbq.upsert_rate(c, "USD", "GBP", 0.8)
    yield c
    c.close()


def seed_series(conn, item_id, prices_days_ago, condition="new",
                source="blended", currency="ILS", confidence="HIGH"):
    for price, days_ago in prices_days_ago:
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        dbq.insert_snapshot(conn, item_id, source, condition, "market", currency,
                            market_price=price, confidence=confidence, scraped_at=ts)
    conn.commit()


class TestLifecycle:
    def test_phases_by_age(self):
        year = datetime.now().year
        assert lifecycle.phase(year)["phase"] == "NEW"
        assert lifecycle.phase(year - 2)["phase"] == "EOL WATCH"
        assert lifecycle.phase(year - 4)["phase"] == "RETIRED_ACCEL"
        assert lifecycle.phase(year - 9)["phase"] == "RETIRED_STABLE"
        assert lifecycle.phase(None)["phase"] is None

    def test_seasonal_theme_retires_earlier(self):
        y = 2020
        assert lifecycle.estimated_retirement_year(y, "Seasonal") == 2022
        assert lifecycle.estimated_retirement_year(y, "Star Wars") == 2023


class TestGrowth:
    def test_observed_growth_annualizes(self, conn):
        dbq.upsert_item(conn, "1111", name="Test set", year=2020)
        # 1000 -> 1100 over exactly one year ≈ +10 %/yr
        seed_series(conn, "1111", [(1000, 365), (1100, 0)])
        g = growth_mod.observed_growth(conn, "1111")
        assert g == pytest.approx(10.0, abs=0.5)

    def test_short_span_returns_none(self, conn):
        dbq.upsert_item(conn, "2222", name="Test", year=2020)
        seed_series(conn, "2222", [(1000, 3), (1010, 0)])
        assert growth_mod.observed_growth(conn, "2222") is None

    def test_growth_vs_retail_converts_currency(self, conn):
        year = datetime.now().year - 4
        dbq.upsert_item(conn, "3333", name="Test", year=year,
                        retail_price=100.0, retail_currency="USD")
        seed_series(conn, "3333", [(700, 0)])  # ILS; retail 100 USD = 350 ILS
        total, cagr = growth_mod.growth_vs_retail(conn, "3333")
        assert total == pytest.approx(100.0, abs=0.1)   # 350 -> 700 = +100 %
        assert cagr == pytest.approx(((2.0) ** 0.25 - 1) * 100, abs=0.1)


class TestBlend:
    def test_confidence_weighted_blend_with_conversion(self, conn):
        dbq.upsert_item(conn, "4444", name="Test", year=2018)
        seed_series(conn, "4444", [(3500, 0)], source="bricklink",
                    currency="ILS", confidence="HIGH")     # 3500 ILS, w=3
        seed_series(conn, "4444", [(1000, 0)], source="ebay",
                    currency="USD", confidence="MEDIUM")   # =3500 ILS, w=2
        seed_series(conn, "4444", [(1000, 0)], source="brickowl",
                    currency="GBP", confidence="LOW")      # =4375 ILS, w=1
        value, confidence, per_source = blend(conn, "4444", "new")
        expected = (3500 * 3 + 3500 * 2 + 4375 * 1) / 6
        assert value == pytest.approx(expected, rel=1e-6)
        assert confidence == "HIGH"
        assert set(per_source) == {"bricklink", "ebay", "brickowl"}

    def test_stale_sources_excluded(self, conn):
        dbq.upsert_item(conn, "5555", name="Test", year=2018)
        seed_series(conn, "5555", [(9999, 60)], source="bricklink",
                    currency="ILS", confidence="HIGH")     # 60 days old → ignored
        seed_series(conn, "5555", [(1000, 0)], source="ebay",
                    currency="USD", confidence="MEDIUM")
        value, _, per_source = blend(conn, "5555", "new")
        assert value == pytest.approx(3500.0)
        assert per_source["bricklink"]["stale"] is True

    def test_store_blended_persists_series(self, conn):
        dbq.upsert_item(conn, "6666", name="Test", year=2018)
        seed_series(conn, "6666", [(3500, 0)], source="bricklink",
                    currency="ILS", confidence="HIGH")
        out = store_blended(conn, "6666")
        assert out["new"] == pytest.approx(3500.0)
        row = dbq.latest_snapshot(conn, "6666", "blended", "new")
        assert row["market_price"] == pytest.approx(3500.0)


def seed_sold(conn, item_id, avg, qty=10, condition="new", source="bricklink",
              currency="ILS", days_ago=0, kind="sold"):
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    dbq.insert_snapshot(conn, item_id, source, condition, kind, currency,
                        price_avg=avg, listing_count=qty, total_qty=qty,
                        confidence="MEDIUM", scraped_at=ts)
    conn.commit()


class TestHeadlineValue:
    """The official number is BrickLink's own sold average — nothing else."""

    def test_bricklink_sold_average_wins_over_market_price(self, conn):
        dbq.upsert_item(conn, "7001", name="Test", year=2018)
        seed_sold(conn, "7001", 485.94)                      # sold avg
        seed_series(conn, "7001", [(498.88, 0)], source="bricklink")  # market
        value, confidence, basis, _ = headline_value(conn, "7001", "new")
        assert value == pytest.approx(485.94)
        assert confidence == "HIGH"          # 10 sales
        assert "sold average" in basis

    def test_ebay_and_brickowl_never_move_the_value(self, conn):
        dbq.upsert_item(conn, "7002", name="Test", year=2018)
        seed_sold(conn, "7002", 485.94)
        seed_series(conn, "7002", [(498.88, 0)], source="bricklink")
        # The real 76051 numbers: eBay asks 415 USD (=1453 ILS), BrickOwl 1056.
        seed_series(conn, "7002", [(415.13, 0)], source="ebay", currency="USD")
        seed_series(conn, "7002", [(1056.29, 0)], source="brickowl")
        seed_sold(conn, "7002", 999.0, source="ebay")
        value, _, _, _ = headline_value(conn, "7002", "new")
        assert value == pytest.approx(485.94), "only BrickLink sets the value"

        # …but they are still shown side by side for reference.
        _, _, per_source = blend(conn, "7002", "new")
        assert set(per_source) == {"bricklink", "ebay", "brickowl"}
        assert per_source["ebay"]["excluded"] is True
        assert per_source["brickowl"]["excluded"] is True
        assert per_source["bricklink"]["excluded"] is False

    def test_falls_back_to_market_then_asks_when_never_sold(self, conn):
        dbq.upsert_item(conn, "7003", name="Test", year=2026)
        seed_series(conn, "7003", [(300.0, 0)], source="bricklink")   # market only
        value, _, basis, _ = headline_value(conn, "7003", "new")
        assert value == pytest.approx(300.0)
        assert "no sold history" in basis

        dbq.upsert_item(conn, "7004", name="Test", year=2026)
        seed_sold(conn, "7004", 250.0, kind="stock")                  # asks only
        value, _, basis, _ = headline_value(conn, "7004", "new")
        assert value == pytest.approx(250.0)
        assert "never sold" in basis

    def test_thin_and_stale_evidence_lowers_confidence(self, conn):
        dbq.upsert_item(conn, "7005", name="Test", year=2018)
        seed_sold(conn, "7005", 100.0, qty=2)                # only 2 sales
        assert headline_value(conn, "7005", "new")[1] == "LOW"

        dbq.upsert_item(conn, "7006", name="Test", year=2018)
        seed_sold(conn, "7006", 100.0, qty=50, days_ago=60)  # long stale
        assert headline_value(conn, "7006", "new")[1] == "LOW"

    def test_no_bricklink_data_means_no_value(self, conn):
        dbq.upsert_item(conn, "7007", name="Test", year=2018)
        seed_series(conn, "7007", [(415.13, 0)], source="ebay", currency="USD")
        assert headline_value(conn, "7007", "new")[0] is None

    def test_currency_is_converted_to_the_blend_currency(self, conn):
        dbq.upsert_item(conn, "7008", name="Test", year=2018)
        seed_sold(conn, "7008", 100.0, currency="USD")       # 100 USD @ 3.5
        assert headline_value(conn, "7008", "new")[0] == pytest.approx(350.0)


class TestForecast:
    def _seed(self, conn, item_id, year, growth_yearly_pct=10.0):
        dbq.upsert_item(conn, item_id, name="Test", year=year)
        p0 = 1000.0
        p1 = p0 * (1 + growth_yearly_pct / 100.0)
        seed_series(conn, item_id, [(p0, 365), (p1, 0)])

    def test_retired_accel_beats_new_at_same_growth(self, conn):
        year = datetime.now().year
        self._seed(conn, "7777", year)          # NEW → multiplier 0.5
        self._seed(conn, "8888", year - 4)      # RETIRED_ACCEL → multiplier 1.5
        f_new = forecast_mod.forecast(conn, "7777")
        f_ret = forecast_mod.forecast(conn, "8888")
        assert f_ret["horizons"][5]["value"] > f_new["horizons"][5]["value"]

    def test_growth_clamped(self, conn):
        dbq.upsert_item(conn, "9990", name="Test", year=2020)
        seed_series(conn, "9990", [(100, 365), (900, 0)])  # +800 %/yr → clamped
        f = forecast_mod.forecast(conn, "9990")
        assert f["growth_pct"] == forecast_mod.PARAMS["clamp_max_pct"]

    def test_band_and_horizons(self, conn):
        self._seed(conn, "9991", 2018)
        f = forecast_mod.forecast(conn, "9991")
        assert set(f["horizons"]) == {2, 5}
        h = f["horizons"][2]
        assert h["low"] < h["value"] < h["high"]
        assert h["low"] == pytest.approx(h["value"] * 0.8, abs=0.01)

    def test_no_value_returns_none(self, conn):
        dbq.upsert_item(conn, "9992", name="Test", year=2020)
        assert forecast_mod.forecast(conn, "9992") is None


class TestEbaySoldOnlyPricing:
    """eBay asks are aspirational; once sold data exists it prices on sales."""

    @staticmethod
    def _data():
        L = lambda p: {"qty": 1, "price": p, "status": "complete",
                       "description": "lego set"}
        return {"meta": {},
                "new": {"sold": [L(100), L(110), L(105)], "stock": [L(300), L(320)]},
                "used": {"sold": [], "stock": [L(80)]}}

    def _analyse(self, source):
        from brickonomy.compat import PriceAnalyzer
        from brickonomy.snapshots import _pricing_analysis
        data = self._data()
        full = PriceAnalyzer(data).analyze(0.0, 0.0)
        return _pricing_analysis(source, data, (0.0, 0.0), full), full

    def test_ebay_prices_on_sold_not_asks(self):
        priced, full = self._analyse("ebay")
        assert priced["new"]["market_price"] == 105      # the sold average
        assert full["new"]["market_price"] > 150         # asks drag it upwards
        assert priced["new"]["confidence"] == "MEDIUM"   # 3 sales

    def test_condition_without_sold_data_is_untouched(self):
        priced, full = self._analyse("ebay")
        assert priced["used"]["market_price"] == full["used"]["market_price"]

    def test_other_sources_keep_the_standard_blend(self):
        for source in ("bricklink", "brickowl"):
            priced, full = self._analyse(source)
            assert priced is full


class TestThemeScanScope:
    """A theme scan must cover the theme's minifigs, not just its sets."""

    def _conn(self, tmp_path, monkeypatch):
        from brickonomy import db as dbq
        from brickonomy.config import get_config
        monkeypatch.setattr(get_config(), "db_path", str(tmp_path / "theme.db"))
        conn = dbq.connect()
        dbq.upsert_item(conn, "76031", name="Hulk Buster", theme="Super Heroes Marvel")
        dbq.upsert_item(conn, "sh0167", name="Iron Man", item_type="M")  # no theme
        dbq.upsert_item(conn, "10283", name="Shuttle", theme="Icons")
        dbq.upsert_set_minifigs(conn, "76031",
                                [{"id": "sh0167", "name": "Iron Man", "qty": 1}])
        conn.commit()
        return conn

    def test_targets_include_sets_and_their_figs(self, tmp_path, monkeypatch):
        from brickonomy.refresh import run_refresh
        conn = self._conn(tmp_path, monkeypatch)
        conn.close()

        seen = []
        import brickonomy.refresh as refresh_mod
        monkeypatch.setattr(refresh_mod, "refresh_item",
                            lambda conn, iid, itype, force=False, log=print,
                                   inventory_only=False:
                            (seen.append(iid), {})[1])
        monkeypatch.setattr(refresh_mod, "polite_sleep", lambda: None)
        run_refresh(scope="theme", theme="Super Heroes Marvel", log=lambda *a: None)

        assert seen == ["76031", "sh0167"]     # sets first, then their figs
        assert "10283" not in seen             # other themes untouched
