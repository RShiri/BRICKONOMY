"""Bridge to the root-level scripts (which are not a package).

The repo root holds pricing_engine.py / scraper.py / database.py as loose
modules. This shim puts the repo root on sys.path exactly once and re-exports
the pieces brickonomy reuses, so every other brickonomy module imports from
here instead of reaching into the root directly.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pricing_engine import PriceAnalyzer  # noqa: E402

# Root Database opens DB_NAME relative to the process cwd; pin it to the repo
# root so brickonomy commands work from any directory without breaking the
# root scripts (they resolve to the same file when run from the repo root).
from database import Database  # noqa: E402

Database.DB_NAME = str(REPO_ROOT / "bricklink_data.db")

LEGACY_DB_PATH = Path(Database.DB_NAME)


def get_bricklink_scraper():
    """Import lazily: root scraper.py needs selenium at import time."""
    from scraper import BrickLinkScraper
    return BrickLinkScraper


__all__ = ["REPO_ROOT", "LEGACY_DB_PATH", "PriceAnalyzer", "Database", "get_bricklink_scraper"]
