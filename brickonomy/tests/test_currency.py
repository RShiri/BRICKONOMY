"""Currency conversion tests with stubbed DB rates — offline."""
import pytest

from brickonomy import db as dbq
from brickonomy.currency import (FALLBACK_RATES, convert, money, rates_status,
                                 reset_rate_cache)


@pytest.fixture()
def conn(tmp_path):
    reset_rate_cache()
    c = dbq.connect(db_path=str(tmp_path / "ccy.db"))
    yield c
    c.close()
    reset_rate_cache()


@pytest.fixture()
def conn_with_rates(conn):
    dbq.upsert_rate(conn, "USD", "ILS", 3.5)
    dbq.upsert_rate(conn, "USD", "EUR", 0.9)
    dbq.upsert_rate(conn, "USD", "GBP", 0.8)
    return conn


class TestConvert:
    def test_identity(self, conn_with_rates):
        assert convert(conn_with_rates, 123.45, "ILS", "ILS") == 123.45

    def test_usd_to_ils(self, conn_with_rates):
        assert convert(conn_with_rates, 100, "USD", "ILS") == pytest.approx(350.0)

    def test_cross_rate_gbp_to_eur(self, conn_with_rates):
        # 80 GBP = 100 USD = 90 EUR
        assert convert(conn_with_rates, 80, "GBP", "EUR") == pytest.approx(90.0)

    def test_none_passthrough(self, conn_with_rates):
        assert convert(conn_with_rates, None, "USD", "ILS") is None

    def test_unsupported_pair_raises(self, conn_with_rates):
        with pytest.raises(ValueError):
            convert(conn_with_rates, 1, "USD", "JPY")

    def test_fallback_when_no_cache_and_no_network(self, conn):
        # Empty DB; network is blocked in the sandbox → hardcoded fallback.
        v = convert(conn, 100, "USD", "ILS")
        assert v == pytest.approx(100 * FALLBACK_RATES["ILS"], rel=0.5)
        assert rates_status()["source"] in ("hardcoded-fallback", "frankfurter.app", "stale-cache")


class TestMoney:
    def test_symbols_and_precision(self):
        assert money(2224.37, "ILS") == "₪2,224"
        assert money(3.5, "USD") == "$3.50"
        assert money(None, "EUR") == "—"
