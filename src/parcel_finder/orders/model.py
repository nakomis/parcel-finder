"""Orders, whichever shop they came from."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Order:
    shop: str  # "aliexpress" | "amazon" | "ebay"
    order_id: str
    title: str
    ordered_at: datetime
    status: str
    product_id: str | None = None
    thumbnail_urls: list[str] = field(default_factory=list)


@dataclass
class OrderMatch:
    order: Order
    confidence: str
    reason: str
