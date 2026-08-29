"""Central configuration.

Values come from, in increasing priority:
  1. dataclass defaults below
  2. brickonomy/config.json (optional, see config.example.json)
  3. BRICKONOMY_* environment variables
"""
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from .compat import REPO_ROOT

CONFIG_JSON = Path(__file__).resolve().parent / "config.json"

SUPPORTED_CURRENCIES = ("ILS", "USD", "EUR", "GBP")


@dataclass
class Config:
    display_currency: str = "ILS"
    db_path: str = str(REPO_ROOT / "brickonomy.db")
    legacy_db_path: str = str(REPO_ROOT / "bricklink_data.db")
    portfolio_csv: str = str(REPO_ROOT / "BrickEconomy-Sets(2).csv")
    scrape_ttl_days: float = 3.0
    # Opening a set page queues a scan when its newest snapshot is older than
    # this (or it has none at all). 0 disables the behaviour entirely.
    auto_scan_on_view_days: float = 30.0
    request_delay_range: list = field(default_factory=lambda: [2.0, 5.0])
    sources_enabled: list = field(default_factory=lambda: ["bricklink", "ebay", "brickowl"])
    scrape_engine: str = "selenium"   # or "playwright" (pip install playwright)
    # Chrome profile used for eBay. Signing in there (python -m
    # brickonomy.ebay_login) unlocks eBay's sold/completed search, which is
    # closed to anonymous clients. "" disables it and keeps eBay asks-only.
    ebay_profile_dir: str = str(REPO_ROOT / ".chrome-ebay")
    # Once eBay sold data is available, price eBay from sold listings alone
    # instead of PriceAnalyzer's 70/30 sold-and-asks blend. eBay asks are
    # aspirational; its sales are the honest number.
    ebay_price_from_sold_only: bool = True
    # Importing to Israel: VAT charged on arrival, plus a flat shipping
    # allowance. The shipping figure is a starting point the set page lets you
    # change per item — courier rates vary far more than the tax does.
    import_vat_pct: float = 18.0
    import_shipping_usd: float = 25.0
    rates_ttl_hours: float = 24.0
    fixture_mode: bool = False
    fixtures_dir: str = str(Path(__file__).resolve().parent / "tests" / "fixtures")

    def validate(self):
        if self.display_currency not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"display_currency must be one of {SUPPORTED_CURRENCIES}, got {self.display_currency!r}"
            )
        return self


def _coerce(value: str, target_type):
    if target_type is bool:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if target_type is float:
        return float(value)
    if target_type is list:
        return [x.strip() for x in value.split(",") if x.strip()]
    return value


def load_config() -> Config:
    cfg = Config()

    if CONFIG_JSON.exists():
        data = json.loads(CONFIG_JSON.read_text())
        for f in fields(Config):
            if f.name in data:
                setattr(cfg, f.name, data[f.name])

    for f in fields(Config):
        env_key = f"BRICKONOMY_{f.name.upper()}"
        if env_key in os.environ:
            target = type(getattr(cfg, f.name))
            setattr(cfg, f.name, _coerce(os.environ[env_key], target))

    return cfg.validate()


_config = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
