from datetime import datetime, timedelta, timezone

from parcel_finder.identify import Identification
from parcel_finder.orders import match
from parcel_finder.orders.model import Order

NOW = datetime(2026, 10, 22, 17, 0, tzinfo=timezone.utc)
IDENT = Identification("USB cable tester", "medium", "labels", "haiku", None)


def order(title, days_ago=3, status="shipped"):
    return Order("amazon", "", title, NOW - timedelta(days=days_ago), status)


def test_match_returns_order_haiku_picks(monkeypatch, photo_fixture):
    monkeypatch.setattr(match.haiku, "ask_json", lambda *a, **k: {"index": 1, "confidence": "high", "reason": "ports"})
    r = match.match_order(photo_fixture, IDENT, [order("Buzzer"), order("USB cable tester board")], now=NOW)
    assert r.order.title == "USB cable tester board" and r.confidence == "high"


def test_match_returns_none_when_haiku_says_none(monkeypatch, photo_fixture):
    monkeypatch.setattr(match.haiku, "ask_json", lambda *a, **k: {"index": -1, "confidence": "low", "reason": "no"})
    assert match.match_order(photo_fixture, IDENT, [order("Buzzer")], now=NOW) is None


def test_match_drops_long_delivered_orders(monkeypatch, photo_fixture):
    seen = {}
    monkeypatch.setattr(match.haiku, "ask_json",
                        lambda prompt, images, schema, **k: seen.setdefault("p", prompt) and {"index": -1, "confidence": "low", "reason": ""})
    match.match_order(photo_fixture, IDENT, [order("Old drill", days_ago=30, status="delivered"), order("Buzzer")], now=NOW)
    assert "Old drill" not in seen["p"] and "Buzzer" in seen["p"]


def test_match_with_no_candidates_skips_haiku(monkeypatch, photo_fixture):
    monkeypatch.setattr(match.haiku, "ask_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert match.match_order(photo_fixture, IDENT, [], now=NOW) is None
