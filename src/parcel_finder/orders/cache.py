"""Local store of recent orders (SQLite): order emails from Gmail in weekly windows (see
gmail.recent_orders), and AliExpress order details from the API. The hourly refresh job fills it;
photo runs only read it."""
import fcntl
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config
from . import gmail
from .model import Order

STORE_DIR = config.HOME / "Library/Application Support/parcel-finder"
CACHE_FILE = STORE_DIR / "orders.sqlite"
LEGACY_JSON = STORE_DIR / "orders.json"  # the JSON cache before HOME-538, imported once
WINDOW = timedelta(days=7)
STALE_AFTER = timedelta(hours=2)  # longer than the hourly refresh timer (launchd/…refresh.plist)
AE_WORKERS = 8
# AliExpress statuses after which an order no longer changes: fetched once, never again.
AE_FINAL = {"FINISH"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    shop TEXT NOT NULL, key TEXT NOT NULL, order_id TEXT NOT NULL, title TEXT NOT NULL,
    ordered_at TEXT NOT NULL, status TEXT NOT NULL, product_id TEXT, thumbnail_urls TEXT NOT NULL,
    PRIMARY KEY (shop, key));
CREATE TABLE IF NOT EXISTS ae_ids (order_id TEXT PRIMARY KEY, urls TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ae_detail (
    order_id TEXT PRIMARY KEY, status TEXT NOT NULL, lines TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def windows(start: date, end: date) -> list[tuple[date, date]]:
    """[start, end] inclusive as half-open weekly windows; the last one ends the day after `end`."""
    out, a, stop = [], start, end + timedelta(days=1)
    while a < stop:
        b = min(a + WINDOW, stop)
        out.append((a, b))
        a = b
    return out


def _order_row(o: Order) -> tuple:
    return (o.shop, o.order_id or o.title, o.order_id, o.title, o.ordered_at.isoformat(), o.status,
            o.product_id, json.dumps(o.thumbnail_urls))


def _order(row) -> Order:
    shop, _, oid, title, at, status, pid, thumbs = row
    return Order(shop, oid, title, datetime.fromisoformat(at), status, pid, json.loads(thumbs))


class OrderCache:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.executescript(SCHEMA)
        if fresh and path == CACHE_FILE and LEGACY_JSON.exists():
            self._import_json(LEGACY_JSON)

    def _import_json(self, legacy: Path) -> None:
        data = json.loads(legacy.read_text())
        orders = [Order(**{**o, "ordered_at": datetime.fromisoformat(o["ordered_at"])}) for o in data.get("orders", [])]
        if data.get("synced_to"):
            self.merge(orders, data.get("ae_ids", {}), date.fromisoformat(data["synced_to"]))

    def _meta(self, k: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row[0] if row else None

    @property
    def orders(self) -> list[Order]:
        return [_order(r) for r in self.db.execute("SELECT * FROM orders")]

    @property
    def ae_ids(self) -> dict[str, list[str]]:
        return {oid: json.loads(urls) for oid, urls in self.db.execute("SELECT order_id, urls FROM ae_ids")}

    @property
    def synced_to(self) -> date | None:
        v = self._meta("synced_to")
        return date.fromisoformat(v) if v else None

    @property
    def synced_at(self) -> datetime | None:
        v = self._meta("synced_at")
        return datetime.fromisoformat(v) if v else None

    @synced_at.setter
    def synced_at(self, when: datetime) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('synced_at', ?)", (when.isoformat(),))

    def merge(self, orders: list[Order], ae_ids: dict[str, list[str]], synced_to: date) -> None:
        known = self.ae_ids
        with self.db:  # one transaction: a crash leaves the store as it was
            # Later emails carry the latest status.
            self.db.executemany("INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                [_order_row(o) for o in orders])
            self.db.executemany("INSERT OR REPLACE INTO ae_ids VALUES (?, ?)",
                                [(oid, json.dumps(list(dict.fromkeys(known.get(oid, []) + urls))))
                                 for oid, urls in ae_ids.items()])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('synced_to', ?)", (synced_to.isoformat(),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('synced_at', ?)", (datetime.now().isoformat(),))

    @property
    def ae_problem(self) -> str | None:
        """Why the last AliExpress sync couldn't fetch anything (e.g. the tokens lapsed), or None."""
        return self._meta("ae_problem") or None

    def set_ae_problem(self, problem: str | None) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('ae_problem', ?)", (problem or "",))

    def stale(self) -> bool:
        return not self.synced_at or datetime.now() - self.synced_at > STALE_AFTER

    def ae_details(self) -> dict[str, tuple[str, list[Order]]]:
        """{order_id: (status, live order lines)}; an order with no live lines was cancelled."""
        return {oid: (status, [Order(**{**line, "ordered_at": datetime.fromisoformat(line["ordered_at"])})
                               for line in json.loads(lines)])
                for oid, status, lines in self.db.execute("SELECT order_id, status, lines FROM ae_detail")}

    def put_ae_detail(self, oid: str, status: str, lines: list[Order]) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO ae_detail VALUES (?, ?, ?, ?)", (
                oid, status, json.dumps([{**o.__dict__, "ordered_at": o.ordered_at.isoformat()} for o in lines]),
                datetime.now().isoformat()))


@contextmanager
def _sync_lock(cache: OrderCache):
    """One sync at a time (the hourly job and a run's background top-up can overlap); a second one
    simply skips, since the first is already doing its work."""
    with open(cache.path.with_suffix(".lock"), "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def sync_gmail(cache: OrderCache, today: date | None = None) -> None:
    today = today or date.today()
    start = (cache.synced_to - timedelta(days=1)) if cache.synced_to else today - timedelta(days=config.ORDER_WINDOW_DAYS)
    for a, b in windows(start, today):
        orders, ae_ids = gmail.recent_orders(a, b)
        cache.merge(orders, ae_ids, synced_to=min(b - timedelta(days=1), today))


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def ae_ids_to_fetch(cache: OrderCache, now: datetime | None = None) -> list[str]:
    """AliExpress orders in the window whose details are missing or could still change."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.ORDER_WINDOW_DAYS)
    details = cache.ae_details()
    dated = {o.order_id: o for o in cache.orders if o.shop == "aliexpress"}
    ids = [oid for oid, o in dated.items() if _aware(o.ordered_at) >= cutoff]
    ids += [oid for oid in cache.ae_ids if oid not in dated]  # known only from thumbnails (older caches)
    return [oid for oid in dict.fromkeys(ids)
            if oid not in details or (details[oid][1] and details[oid][0] not in AE_FINAL)]


def sync_aliexpress(cache: OrderCache, client) -> dict[str, str]:
    """Fetch the details that can still change, in parallel; returns {order_id: problem}.
    Reauthorise propagates (nothing can be fetched)."""
    from .aliexpress import ApiError
    ids = ae_ids_to_fetch(cache)
    client._access_token()  # refresh once up front, not in every worker

    def fetch(oid):
        try:
            return oid, client.order_detail(oid), None
        except (ApiError, KeyError, ValueError, OSError) as e:  # URLError is an OSError
            return oid, None, str(e)

    problems = {}
    with ThreadPoolExecutor(AE_WORKERS) as pool:
        for oid, detail, problem in pool.map(fetch, ids):
            if detail is None:
                problems[oid] = problem
            else:
                cache.put_ae_detail(oid, *detail)
    return problems


def sync(cache: OrderCache, today: date | None = None, aliexpress=None) -> dict[str, str] | None:
    """Gmail, then AliExpress details. `aliexpress` is a client, or a callable making one (it may
    raise Reauthorise or MissingSecret: recorded as the store's ae_problem, Gmail still synced).
    Returns {order_id: problem}, or None if another sync holds the lock."""
    from .. import config as cfg
    from .aliexpress import Reauthorise
    with _sync_lock(cache) as mine:
        if not mine:
            return None
        sync_gmail(cache, today)
        if aliexpress is None:
            return {}
        try:
            client = aliexpress() if callable(aliexpress) else aliexpress
            problems = sync_aliexpress(cache, client)
        except (Reauthorise, cfg.MissingSecret) as e:
            cache.set_ae_problem(str(e))
            return {}
        cache.set_ae_problem(None)
        return problems
