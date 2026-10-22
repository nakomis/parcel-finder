import base64
import json
from datetime import date, datetime, timezone

from parcel_finder.orders import gmail
from parcel_finder.orders.gmail import extract_thumbnails, parse_subject


def test_amazon_subjects():
    assert parse_subject("auto-confirm@amazon.co.uk", "Ordered: ‘Power Rotary Tool Kit 170W:...’") == \
        ("amazon", "", "Power Rotary Tool Kit 170W", "ordered")
    assert parse_subject("shipment-tracking@amazon.co.uk", "Out for delivery: 2 ‘48 Pack Sound Proofing...’ and 1 more item") == \
        ("amazon", "", "48 Pack Sound Proofing", "shipped")
    assert parse_subject("order-update@amazon.co.uk", "Delivered: 2 \"48 Pack Sound Proofing...\" and 1 more item") == \
        ("amazon", "", "48 Pack Sound Proofing", "delivered")


def test_ebay_subjects():
    assert parse_subject("ebay@ebay.com", "Order confirmed: Kingston Fury Impact...") == \
        ("ebay", "", "Kingston Fury Impact", "ordered")
    assert parse_subject("ebay@ebay.com", "📦PACKAGE DELIVERED: ANYCUBIC Alcohol Buc...") == \
        ("ebay", "", "ANYCUBIC Alcohol Buc", "delivered")
    assert parse_subject("ebay@ebay.com", "🚚 Order update: Kingston Fury Impact 32GB...") == \
        ("ebay", "", "Kingston Fury Impact 32GB", "shipped")
    assert parse_subject("ebay@ebay.com", "Your package is now with its carrier!") is None


def test_aliexpress_subjects():
    s = "transaction@notice.aliexpress.com"
    assert parse_subject(s, "Order 3076931683840500: order shipped") == ("aliexpress", "3076931683840500", "", "shipped")
    assert parse_subject(s, "Order 3077139497790500: how did it go?") == ("aliexpress", "3077139497790500", "", "delivered")
    assert parse_subject(s, "Order 3076931683890500: order confirmed") == ("aliexpress", "3076931683890500", "", "ordered")
    assert parse_subject(s, "Package T02SHA0838468224: in your country/region") is None


def test_unrelated_sender_ignored():
    assert parse_subject("news@example.com", "Ordered: ‘Thing’") is None


def test_extract_thumbnails():
    html = ('<img src="https://ae-pic-a1.aliexpress-media.com/kf/S5ae0.jpg_220x220.jpg">'
            '<img src="https://ae01.alicdn.com/kf/HTB1M.png"><img src="https://img.alicdn.com/logo.png">'
            '<img src="https://ae-pic-a1.aliexpress-media.com/kf/S5ae0.jpg_220x220.jpg">')
    assert extract_thumbnails(html) == ["https://ae-pic-a1.aliexpress-media.com/kf/S5ae0.jpg_220x220.jpg",
                                        "https://ae01.alicdn.com/kf/HTB1M.png"]


def _msg(mid, sender, subject, when, html=None):
    m = {"id": mid, "internalDate": str(int(when.timestamp() * 1000)),
         "payload": {"headers": [{"name": "From", "value": f"X <{sender}>"}, {"name": "Subject", "value": subject}]}}
    if html:
        m["payload"]["mimeType"] = "text/html"
        m["payload"]["body"] = {"data": base64.urlsafe_b64encode(html.encode()).decode()}
    return m


def test_recent_orders_groups_and_keeps_earliest_time(monkeypatch):
    t1 = datetime(2026, 10, 21, 11, 5, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 21, 18, 55, tzinfo=timezone.utc)
    t3 = datetime(2026, 10, 19, 21, 55, tzinfo=timezone.utc)
    msgs = {"a": _msg("a", "auto-confirm@amazon.co.uk", "Ordered: ‘Power Rotary Tool Kit 170W:...’", t1),
            "b": _msg("b", "order-update@amazon.co.uk", "Delivered: ‘Power Rotary Tool Kit 170W:...’", t2),
            "c": _msg("c", "transaction@notice.aliexpress.com", "Order 3076931683840500: order shipped", t3,
                      html='<img src="https://ae01.alicdn.com/kf/X.jpg">')}
    queries = []

    class Fake:
        def search(self, q):
            queries.append(q)
            return list(msgs)

        def message(self, mid, full=False):
            return msgs[mid]

    monkeypatch.setattr(gmail, "_client", lambda: Fake())
    orders, ae = gmail.recent_orders(date(2026, 10, 15), date(2026, 10, 22))
    assert queries == ["category:purchases after:2026/10/15 before:2026/10/22"]
    amazon = [o for o in orders if o.shop == "amazon"]
    assert [(o.title, o.status, o.ordered_at) for o in amazon] == [("Power Rotary Tool Kit 170W", "delivered", t1)]
    assert ae == {"3076931683840500": ["https://ae01.alicdn.com/kf/X.jpg"]}
    assert [(o.order_id, o.ordered_at) for o in orders if o.shop == "aliexpress"] == [("3076931683840500", t3)]
