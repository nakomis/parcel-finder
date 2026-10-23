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
    c = OrderCache(tmp_path / "orders.sqlite")
    c.merge([Order("amazon", "", "Drill", T, "ordered")], {"111": ["u1"]}, synced_to=date(2026, 10, 21))
    c.merge([Order("amazon", "", "Drill", T, "delivered")], {"111": ["u1", "u2"], "222": []}, synced_to=date(2026, 10, 22))
    c2 = OrderCache(tmp_path / "orders.sqlite")
    assert [(o.title, o.status) for o in c2.orders] == [("Drill", "delivered")]
    assert c2.ae_ids == {"111": ["u1", "u2"], "222": []}
    assert c2.synced_to == date(2026, 10, 22)


def test_sync_fetches_only_since_last_sync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: calls.append((a, b)) or ([], {}))
    c = OrderCache(tmp_path / "orders.sqlite")
    c.merge([], {}, synced_to=date(2026, 10, 20))
    cache_mod.sync_gmail(c, today=date(2026, 10, 22))
    assert calls == [(date(2026, 10, 19), date(2026, 10, 23))]   # one day's overlap, through today


def test_first_sync_covers_order_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: calls.append((a, b)) or ([], {}))
    cache_mod.sync_gmail(OrderCache(tmp_path / "orders.sqlite"), today=date(2026, 10, 22))
    assert calls[0][0] == date(2026, 10, 22) - timedelta(days=60) and len(calls) == 9


NOW = datetime.now(timezone.utc)


class FakeAe:
    def __init__(self, details):
        self.details, self.asked = details, []

    def _access_token(self):
        return "t"

    def order_detail(self, oid):
        self.asked.append(oid)
        return self.details[oid]


def _store(tmp_path, *ids):
    c = OrderCache(tmp_path / "orders.sqlite")
    c.merge([Order("aliexpress", i, "", NOW - timedelta(days=2), "shipped") for i in ids],
            {i: [] for i in ids}, synced_to=date.today())
    return c


def test_finished_and_cancelled_orders_are_fetched_once(tmp_path):
    c = _store(tmp_path, "done", "cancelled", "moving")
    line = lambda oid, st: Order("aliexpress", oid, "Thing", NOW, st)
    ae = FakeAe({"done": ("FINISH", [line("done", "FINISH")]), "cancelled": ("FINISH", []),
                 "moving": ("WAIT_BUYER_ACCEPT_GOODS", [line("moving", "WAIT_BUYER_ACCEPT_GOODS")])})
    cache_mod.sync_aliexpress(c, ae)
    assert sorted(ae.asked) == ["cancelled", "done", "moving"]
    ae.asked.clear()
    cache_mod.sync_aliexpress(c, ae)
    assert ae.asked == ["moving"]


def test_one_failed_fetch_keeps_the_rest(tmp_path):
    from parcel_finder.orders.aliexpress import ApiError
    c = _store(tmp_path, "good", "bad")

    class Ae(FakeAe):
        def order_detail(self, oid):
            if oid == "bad":
                raise ApiError("network down")
            return super().order_detail(oid)
    problems = cache_mod.sync_aliexpress(c, Ae({"good": ("FINISH", [Order("aliexpress", "good", "X", NOW, "FINISH")])}))
    assert set(c.ae_details()) == {"good"} and "network down" in problems["bad"]


def test_lapsed_tokens_are_recorded_and_gmail_still_syncs(tmp_path, monkeypatch):
    from parcel_finder.orders.aliexpress import Reauthorise
    gmail_calls = []
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: gmail_calls.append(a) or ([], {}))
    c = _store(tmp_path, "1")

    def client():
        raise Reauthorise("refresh token missing or lapsed")
    assert cache_mod.sync(c, aliexpress=client) == {}
    assert gmail_calls and c.ae_problem == "refresh token missing or lapsed"
    cache_mod.sync(c, aliexpress=FakeAe({"1": ("FINISH", [])}))
    assert c.ae_problem is None


def test_a_second_sync_skips_while_one_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod.gmail, "recent_orders", lambda a, b: ([], {}))
    c = _store(tmp_path)
    with cache_mod._sync_lock(c) as mine:
        assert mine and cache_mod.sync(c) is None


def test_store_survives_reopening(tmp_path):
    c = _store(tmp_path, "1")
    c.put_ae_detail("1", "FINISH", [Order("aliexpress", "1", "Buzzer", NOW, "FINISH")])
    c2 = OrderCache(tmp_path / "orders.sqlite")
    assert c2.ae_details()["1"][1][0].title == "Buzzer" and c2.synced_to == date.today()


def test_legacy_json_cache_is_imported_once(tmp_path, monkeypatch):
    import json
    legacy = tmp_path / "orders.json"
    legacy.write_text(json.dumps({"orders": [{"shop": "amazon", "order_id": "", "title": "Drill",
                                              "ordered_at": T.isoformat(), "status": "shipped"}],
                                  "ae_ids": {"9": ["u"]}, "synced_to": "2026-10-22", "synced_at": T.isoformat()}))
    monkeypatch.setattr(cache_mod, "CACHE_FILE", tmp_path / "orders.sqlite")
    monkeypatch.setattr(cache_mod, "LEGACY_JSON", legacy)
    c = OrderCache(cache_mod.CACHE_FILE)
    assert [o.title for o in c.orders] == ["Drill"] and c.ae_ids == {"9": ["u"]} and c.synced_to == date(2026, 10, 22)


def test_photo_runs_make_no_aliexpress_calls(tmp_path, monkeypatch):
    from parcel_finder import pipeline
    from parcel_finder.orders import aliexpress
    c = _store(tmp_path, "1")
    c.put_ae_detail("1", "WAIT_BUYER_ACCEPT_GOODS", [Order("aliexpress", "1", "Buzzer", NOW, "WAIT_BUYER_ACCEPT_GOODS")])
    monkeypatch.setattr(cache_mod, "OrderCache", lambda: c)
    monkeypatch.setattr(aliexpress, "_post", lambda *a: (_ for _ in ()).throw(AssertionError("network call")))
    monkeypatch.setattr(pipeline, "_start_background_sync", lambda: None)
    found, problem = pipeline.collect_orders()
    assert [o.title for o in found] == ["Buzzer"] and problem is None
