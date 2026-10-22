"""Local cache of recent orders, filled from Gmail in weekly windows (see gmail.recent_orders)."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config
from . import gmail
from .model import Order

CACHE_FILE = config.HOME / "Library/Application Support/parcel-finder/orders.json"
WINDOW = timedelta(days=7)
STALE_AFTER = timedelta(hours=13)  # longer than the 12-hourly refresh timer


def windows(start: date, end: date) -> list[tuple[date, date]]:
    """[start, end] inclusive as half-open weekly windows; the last one ends the day after `end`."""
    out, a, stop = [], start, end + timedelta(days=1)
    while a < stop:
        b = min(a + WINDOW, stop)
        out.append((a, b))
        a = b
    return out


class OrderCache:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.orders = [Order(**{**o, "ordered_at": datetime.fromisoformat(o["ordered_at"])})
                       for o in data.get("orders", [])]
        self.ae_ids: dict[str, list[str]] = data.get("ae_ids", {})
        self.synced_to = date.fromisoformat(data["synced_to"]) if data.get("synced_to") else None
        self.synced_at = datetime.fromisoformat(data["synced_at"]) if data.get("synced_at") else None

    def merge(self, orders: list[Order], ae_ids: dict[str, list[str]], synced_to: date) -> None:
        by_key = {(o.shop, o.order_id or o.title): o for o in self.orders}
        for o in orders:
            by_key[(o.shop, o.order_id or o.title)] = o  # later emails carry the latest status
        self.orders = list(by_key.values())
        for oid, urls in ae_ids.items():
            self.ae_ids[oid] = list(dict.fromkeys(self.ae_ids.get(oid, []) + urls))
        self.synced_to, self.synced_at = synced_to, datetime.now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "orders": [{**o.__dict__, "ordered_at": o.ordered_at.isoformat()} for o in self.orders],
            "ae_ids": self.ae_ids, "synced_to": synced_to.isoformat(),
            "synced_at": self.synced_at.isoformat()}, indent=1))

    def stale(self) -> bool:
        return not self.synced_at or datetime.now() - self.synced_at > STALE_AFTER


def sync(cache: OrderCache, today: date | None = None) -> None:
    today = today or date.today()
    start = (cache.synced_to - timedelta(days=1)) if cache.synced_to else today - timedelta(days=config.ORDER_WINDOW_DAYS)
    for a, b in windows(start, today):
        orders, ae_ids = gmail.recent_orders(a, b)
        cache.merge(orders, ae_ids, synced_to=min(b - timedelta(days=1), today))
