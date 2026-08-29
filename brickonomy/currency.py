"""Currency conversion with a no-key rate source and layered fallbacks.

Rates are stored as USD-base pairs in exchange_rates. Conversion between any
two supported currencies goes through USD cross-rates. Fetch order:
  1. cached DB rates newer than rates_ttl_hours
  2. frankfurter.app (ECB, no API key)
  3. stale cached DB rates (with a staleness flag)
  4. hardcoded last-resort table below
"""
from datetime import datetime, timedelta

import requests

from . import db as dbq
from .config import get_config

BASE = "USD"
QUOTES = ("ILS", "EUR", "GBP")
FRANKFURTER_URL = "https://api.frankfurter.app/latest"

# Last-resort rates (approximate, mid-2026) used only when no network and no cache.
FALLBACK_RATES = {"ILS": 3.65, "EUR": 0.92, "GBP": 0.78}

_state = {"stale": False, "source": None}
# Rates are needed for every displayed price, so hold them in-process too:
# without this a failing (or blocked) rates API is retried on every call.
_memo = {"rates": None, "at": 0.0}
_MEMO_TTL_SECONDS = 600


def rates_status():
    """UI hint: where the current rates came from and whether they are stale."""
    return dict(_state)


def reset_rate_cache():
    """Drop the in-process memo. The memo is global (one DB per process in
    normal use), so tests that swap databases must call this."""
    _memo.update(rates=None, at=0.0)


def _fetch_remote():
    resp = requests.get(
        FRANKFURTER_URL, params={"from": BASE, "to": ",".join(QUOTES)}, timeout=5
    )
    resp.raise_for_status()
    return resp.json()["rates"]


def get_usd_rates(conn) -> dict:
    """Return {quote: rate} with USD base, refreshing per the TTL."""
    import time

    if _memo["rates"] and time.monotonic() - _memo["at"] < _MEMO_TTL_SECONDS:
        return _memo["rates"]

    rates = _load_usd_rates(conn)
    _memo.update(rates=rates, at=time.monotonic())
    return rates


def _load_usd_rates(conn) -> dict:
    cfg = get_config()
    cached = dbq.get_rates(conn, BASE)
    fresh_cutoff = datetime.now() - timedelta(hours=cfg.rates_ttl_hours)

    have_fresh = all(
        q in cached and datetime.fromisoformat(cached[q][1]) > fresh_cutoff
        for q in QUOTES
    )
    if have_fresh:
        _state.update(stale=False, source="cache")
        return {q: cached[q][0] for q in QUOTES}

    try:
        remote = _fetch_remote()
        for q in QUOTES:
            if q in remote:
                dbq.upsert_rate(conn, BASE, q, remote[q])
        _state.update(stale=False, source="frankfurter.app")
        return {q: remote.get(q, FALLBACK_RATES[q]) for q in QUOTES}
    except Exception:
        if all(q in cached for q in QUOTES):
            _state.update(stale=True, source="stale-cache")
            return {q: cached[q][0] for q in QUOTES}
        # Not persisted: the DB cache is for real rates only, so the UI can
        # keep telling the truth about where today's numbers came from.
        _state.update(stale=True, source="hardcoded-fallback")
        return dict(FALLBACK_RATES)


def convert(conn, amount: float, from_ccy: str, to_ccy: str) -> float:
    if amount is None:
        return None
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return amount
    rates = get_usd_rates(conn)
    rates = {BASE: 1.0, **rates}
    if from_ccy not in rates or to_ccy not in rates:
        raise ValueError(f"unsupported currency pair {from_ccy}->{to_ccy}")
    usd = amount / rates[from_ccy]
    return usd * rates[to_ccy]


CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}


def detect_currency(price_text: str, default: str = "ILS") -> str:
    """Map a marketplace price cell (e.g. 'US $12.34', '~ILS 45.00') to a code.

    Order matters: country prefixes are checked before the bare '$', since
    BrickLink renders Canadian and Australian dollars as 'CA $' / 'AU $'.
    """
    p = (price_text or "").upper()
    if "ILS" in p or "₪" in p:
        return "ILS"
    if "US" in p:
        return "USD"
    if "CA" in p:
        return "CAD"
    if "AU" in p:
        return "AUD"
    if "EU" in p or "€" in p:
        return "EUR"
    if "GB" in p or "£" in p:
        return "GBP"
    if "$" in p:
        return "USD"
    return default


def money(value, ccy: str) -> str:
    """Jinja filter: format an amount with its currency symbol."""
    if value is None:
        return "—"
    sym = CURRENCY_SYMBOLS.get(ccy.upper(), ccy + " ")
    return f"{sym}{value:,.0f}" if abs(value) >= 100 else f"{sym}{value:,.2f}"
