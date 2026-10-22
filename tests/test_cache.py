from datetime import date, datetime, timedelta, timezone

from parcel_finder.orders import cache as cache_mod
from parcel_finder.orders.cache import OrderCache, windows
from parcel_finder.orders.model import Order

T = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def test_windows_split_into_weeks():
    assert windows(date(2026, 8, 23), date(2026, 10, 22)) == [
        (date(2026, 8, 23), date(2026, 8, 30)), (date(2026, 8, 30), date(2026, 9, 6)),
        (date(2026, 9, 6), date(2026, 9, 13)), (date(2026, 9, 13), date(2026, 9, 20)),
        (date(2026, 9, 20), date(2026, 9, 27)), (date(2026, 9, 27), date(2026, 10, 4)),
        (date(2026, 10, 4), date(2026, 10, 11)), (date(2026, 10, 11), date(2026, 10, 18)),
        (date(2026, 10, 18), date(2026, 10, 23))]


def test_cache_round_trip_and_dedup(tmp_path):
    c = OrderCache(tmp_path / "orders.json")
    c.merge([Order("amazon", "", "Drill", T, "ordered")], {"111": ["u1"]}, synced_to=date(2026, 10, 21))
    c.merge([Order("amazon", "", "Drill", T, "delivered")], {"111": ["u1", "u2"], "222": []}, synced_to=date(2026, 10, 22))
    c2 = OrderCache(tmp_path / "orders.json")
    assert [(o.title, o.status) for o in c2.orders] == [("Drill", "delivered")]
    assert c2.ae_ids == {"111": ["u1", "u2"], "222": []}
    assert c2.synced_to == date(2026, 10, 22)


def test_sync_fetches_only_since_last_sync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: calls.append((a, b)) or ([], {}))
    c = OrderCache(tmp_path / "orders.json")
    c.merge([], {}, synced_to=date(2026, 10, 20))
    cache_mod.sync(c, today=date(2026, 10, 22))
    assert calls == [(date(2026, 10, 19), date(2026, 10, 23))]   # one day's overlap, through today


def test_first_sync_covers_order_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: calls.append((a, b)) or ([], {}))
    cache_mod.sync(OrderCache(tmp_path / "orders.json"), today=date(2026, 10, 22))
    assert calls[0][0] == date(2026, 10, 22) - timedelta(days=60) and len(calls) == 9
