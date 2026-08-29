"""Lightweight dataclasses used across scrapers and analytics."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """One price row in the normalized scrape shape (matches root PriceAnalyzer)."""
    qty: int
    price: float
    status: str = "complete"          # 'complete' | 'incomplete'
    description: str = ""             # scanned by PriceAnalyzer's blacklist

    def as_dict(self):
        return {"qty": self.qty, "price": self.price,
                "status": self.status, "description": self.description}


@dataclass
class ScrapeResult:
    """Normalized output of every scraper: the root PriceAnalyzer shape plus
    source/currency, and an error field for graceful degradation."""
    item_id: str
    source: str
    currency: str
    meta: dict = field(default_factory=dict)
    new: dict = field(default_factory=lambda: {"sold": [], "stock": []})
    used: dict = field(default_factory=lambda: {"sold": [], "stock": []})
    error: Optional[str] = None

    def analyzer_input(self) -> dict:
        return {"meta": self.meta, "new": self.new, "used": self.used}

    @property
    def is_empty(self) -> bool:
        return not (self.new["sold"] or self.new["stock"]
                    or self.used["sold"] or self.used["stock"])


@dataclass
class SeriesPoint:
    ts: str
    value: float
    currency: str
    confidence: str = "MEDIUM"
