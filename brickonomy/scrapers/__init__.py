from .bricklink import BrickLinkSource
from .brickowl import BrickOwlSource
from .ebay import EbaySource

SOURCES = {
    "bricklink": BrickLinkSource,
    "ebay": EbaySource,
    "brickowl": BrickOwlSource,
}
