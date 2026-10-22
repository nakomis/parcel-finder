import json
import subprocess
from datetime import date, datetime

from parcel_finder.orders import gmail

RECORDED = {"structured_output": {"orders": [
    {"shop": "amazon", "order_id": "", "title": "Power Rotary Tool Kit 170W", "ordered_at": "2026-10-21T11:05:53Z",
     "status": "delivered", "thumbnail_urls": []},
    {"shop": "ebay", "order_id": "", "title": "Kingston Fury Impact 32GB", "ordered_at": "2026-10-21T20:07:55Z",
     "status": "ordered", "thumbnail_urls": []},
    {"shop": "aliexpress", "order_id": "3076931683840500", "title": "", "ordered_at": "2026-10-19T21:55:33Z",
     "status": "shipped", "thumbnail_urls": ["https://ae01.alicdn.com/kf/x.jpg"]},
]}}


def fake_run(record):
    def run(argv, **kw):
        record.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(RECORDED), "")
    return run


def test_recent_orders_parses_claude_json(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    orders, ae_ids = gmail.recent_orders(date(2026, 10, 15), date(2026, 10, 22))
    assert [o.title for o in orders] == ["Power Rotary Tool Kit 170W", "Kingston Fury Impact 32GB"]
    assert orders[0].ordered_at == datetime.fromisoformat("2026-10-21T11:05:53+00:00")
    assert ae_ids == {"3076931683840500": ["https://ae01.alicdn.com/kf/x.jpg"]}


def test_recent_orders_never_uses_strict_mcp_config(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    gmail.recent_orders(date(2026, 10, 15), date(2026, 10, 22))
    argv = calls[0]
    assert "--strict-mcp-config" not in argv and "--tools" not in argv
    assert "--json-schema" in argv and "category:purchases after:2026/10/15 before:2026/10/22" in argv[-1]


def test_missing_structured_output_is_clear_error(monkeypatch):
    import pytest
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, json.dumps({"subtype": "error_max_turns", "is_error": True}), ""))
    with pytest.raises(RuntimeError, match="error_max_turns"):
        gmail.recent_orders(date(2026, 10, 15), date(2026, 10, 22))
