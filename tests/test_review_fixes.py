"""Tests for the final review's Critical and Important findings (C1, I1-I10)."""
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from parcel_finder import config, delivery, haiku, pipeline, sessions, ui
from parcel_finder.identify import Identification
from parcel_finder.orders import aliexpress, cache, gmail
from parcel_finder.orders.model import Order, OrderMatch
from parcel_finder.sessions import Candidate, SessionChoice

FIXTURE = Path(__file__).parent / "fixtures" / "ae_order.json"
IDENT = Identification("buzzer", "medium", "label", "haiku", None)


# C1: AliExpress gmt_create is Asia/Shanghai; every Order.ordered_at is aware UTC.
def test_aliexpress_order_time_is_aware_utc(monkeypatch):
    monkeypatch.setattr(aliexpress, "_post", lambda url, p: json.loads(FIXTURE.read_text()))
    store = aliexpress.MemoryStore({"access-token": "a", "refresh-token": "r", "token-meta": json.dumps(
        {"expire_time": 9e15, "refresh_token_valid_time": 9e15})})
    o = aliexpress.AliExpress("k", "s", store=store).orders(["1"])[0]
    assert o.ordered_at == datetime(2026, 10, 19, 21, 54, 41, tzinfo=timezone.utc)


def test_rank_accepts_aliexpress_order_time(monkeypatch):
    around = datetime(2026, 10, 19, 21, 54, 41, tzinfo=timezone.utc)
    hits = [{"session_id": "a", "name": "n", "created_at_utc": "2026-10-19T21:30:00Z", "context": []}]
    assert sessions.rank(hits, around)[0].score > 1


def test_gmail_order_times_are_aware_utc(monkeypatch):
    msg = {"id": "a", "internalDate": "1792580753000", "payload": {"headers": [
        {"name": "From", "value": "auto-confirm@amazon.co.uk"}, {"name": "Subject", "value": "Ordered: \u2018Drill\u2019"}]}}

    class Fake:
        def search(self, q):
            return ["a"]

        def message(self, mid, full=False):
            return msg
    monkeypatch.setattr(gmail, "_client", lambda: Fake())
    orders, _ = gmail.recent_orders(date(2026, 10, 15), date(2026, 10, 22))
    assert orders[0].ordered_at.tzinfo is not None


# I1: auto-proceed needs a high-confidence order match and a winner with real evidence.
def cand(sid, score, hits=5):
    return Candidate(sid, sid, "/p", datetime(2026, 10, 19, tzinfo=timezone.utc), hits, "s", score)


def test_low_confidence_order_match_is_not_clear(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "a", "confidence": "high", "reason": ""})
    m = OrderMatch(Order("aliexpress", "1", "Buzzer", datetime.now(timezone.utc), "x"), "low", "guess")
    assert not sessions.choose(IDENT, m, [cand("a", 9), cand("b", 1)]).clear


def test_single_hit_winner_is_not_clear(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "a", "confidence": "high", "reason": ""})
    m = OrderMatch(Order("aliexpress", "1", "Buzzer", datetime.now(timezone.utc), "x"), "high", "same")
    assert not sessions.choose(IDENT, m, [cand("a", 4, hits=1)]).clear


# I2: only JSON from local callers, only photos under the ScreenEye folder.
@pytest.fixture
def server(tmp_path):
    import threading
    from parcel_finder.service import make_server
    srv = make_server(lambda p: None, port=0, allowed_root=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()
    srv.server_close()


def _post(url, body, ctype="application/json", host=None):
    req = urllib.request.Request(url + "/photo", body.encode(), {"Content-Type": ctype})
    if host:
        req.add_header("Host", host)
    return urllib.request.urlopen(req, timeout=5)


def test_text_plain_post_is_rejected(server):
    url, root = server
    (root / "a.jpg").write_bytes(b"x")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, json.dumps({"path": str(root / "a.jpg")}), ctype="text/plain")
    assert e.value.code == 415


def test_foreign_host_header_is_rejected(server):
    url, root = server
    (root / "a.jpg").write_bytes(b"x")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, json.dumps({"path": str(root / "a.jpg")}), host="evil.example:8765")
    assert e.value.code == 403


def test_photo_outside_allowed_root_is_rejected(server, tmp_path_factory):
    url, _ = server
    other = tmp_path_factory.mktemp("elsewhere") / "b.jpg"
    other.write_bytes(b"x")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(url, json.dumps({"path": str(other)}))
    assert e.value.code == 403


# I3: a failed keychain write never carries the secret in its message.
def test_store_secret_failure_hides_value(monkeypatch):
    def boom(argv, **kw):
        raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RuntimeError) as e:
        config.store_secret("svc", "access-token", "SUPERSECRET")
    assert "SUPERSECRET" not in repr(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


# I4: optional stages never kill the run.

def _deps(tmp_path):
    from parcel_finder.delivery import LiveSession
    t = datetime(2026, 10, 19, tzinfo=timezone.utc)
    c = Candidate("sid-a", "ddr-banging", "/p", t, 12, "s", 12.0)
    calls = {"notify": [], "send": []}
    d = pipeline.Deps(
        identify=lambda p, log: IDENT, collect_orders=lambda: ([], None),
        match_order=lambda p, i, o: None, search=lambda q, a: [c],
        choose=lambda i, m, cs: SessionChoice(c, True, cs, ""), ui_choose=lambda cs, item: "sid-a",
        local_transcript=lambda s: Path("/t"), copy_photo=lambda p, s, n: (p, True),
        live_session=lambda s: LiveSession(1, Path("/s"), Path("/k"), "ttys1", Path("/w"), "idle"),
        send=lambda live, text: calls["send"].append(text), raise_tab=lambda t: True,
        resume=lambda *a: None, notify=lambda title, text: calls["notify"].append(text), log_dir=tmp_path)
    return d, calls


def test_collect_orders_exception_continues(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    d = replace(d, collect_orders=lambda: (_ for _ in ()).throw(RuntimeError("claude -p timed out")))
    assert pipeline.run(photo_fixture, d).status == "delivered" and calls["send"]


def test_match_exception_continues(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    d = replace(d, collect_orders=lambda: ([Order("amazon", "", "Drill", datetime.now(timezone.utc), "x")], None),
                match_order=lambda *a: (_ for _ in ()).throw(ValueError("bad thumbnail")))
    assert pipeline.run(photo_fixture, d).status == "delivered"


def test_one_bad_aliexpress_order_keeps_the_rest(monkeypatch):
    good = json.loads(FIXTURE.read_text())

    def post(url, p):
        if json.loads(p["single_order_query"])["order_id"] == "bad":
            raise urllib.error.URLError("network down")
        return good
    monkeypatch.setattr(aliexpress, "_post", post)
    store = aliexpress.MemoryStore({"access-token": "a", "refresh-token": "r", "token-meta": json.dumps(
        {"expire_time": 9e15, "refresh_token_valid_time": 9e15})})
    ae = aliexpress.AliExpress("k", "s", store=store)
    orders, problems = ae.orders_tolerant(["bad", "1"])
    assert [o.order_id for o in orders] == ["1"] and "network down" in problems["bad"]


# I5: a stale cache never blocks a run; it is refreshed in the background.
def test_stale_cache_syncs_in_background(monkeypatch, tmp_path):
    c = cache.OrderCache(tmp_path / "orders.json")
    c.merge([], {}, synced_to=date(2026, 10, 1))
    c.synced_at = datetime.now() - timedelta(days=2)
    started = []
    monkeypatch.setattr(cache, "OrderCache", lambda: c)
    monkeypatch.setattr(cache, "sync", lambda *a, **k: (_ for _ in ()).throw(AssertionError("blocking sync")))
    monkeypatch.setattr(pipeline, "_start_background_sync", lambda: started.append(True))
    monkeypatch.setattr(pipeline, "_aliexpress_orders", lambda ids: ([], None, set()))
    pipeline.collect_orders()
    assert started == [True]


def test_stale_after_is_longer_than_timer():
    import plistlib
    plist = Path(__file__).parents[1] / "launchd" / "com.nakomis.parcel-finder.refresh.plist"
    assert cache.STALE_AFTER > timedelta(seconds=plistlib.loads(plist.read_bytes())["StartInterval"])


# I6: `refresh` syncs Gmail even when AliExpress needs re-authorising.
def test_refresh_syncs_even_when_reauthorise(monkeypatch):
    from parcel_finder import cli
    synced = []

    class Ae:
        @staticmethod
        def from_keychain():
            raise aliexpress.Reauthorise("lapsed")
    monkeypatch.setattr(aliexpress, "AliExpress", Ae)
    monkeypatch.setattr(ui, "notify", lambda *a: None)
    monkeypatch.setattr(cli, "sync_orders", lambda args: synced.append(True) or 0)
    cli.main(["refresh"])
    assert synced == [True]


# I7: with the API unavailable, AliExpress orders still come from the emails.
def test_aliexpress_falls_back_to_email_orders(monkeypatch, tmp_path):
    t = datetime.now(timezone.utc) - timedelta(days=3)
    c = cache.OrderCache(tmp_path / "orders.json")
    c.merge([Order("aliexpress", "777", "", t, "shipped", thumbnail_urls=["https://x/a.jpg"])],
            {"777": ["https://x/a.jpg"]}, synced_to=date.today())
    monkeypatch.setattr(cache, "OrderCache", lambda: c)
    monkeypatch.setattr(pipeline, "_aliexpress_orders", lambda ids: (None, "refresh token missing or lapsed", set(ids)))
    found, problem = pipeline.collect_orders()
    assert [(o.shop, o.order_id) for o in found] == [("aliexpress", "777")] and problem
    assert found[0].thumbnail_urls


# I8: only AliExpress orders inside the window are looked up.
def test_only_recent_aliexpress_ids_are_queried(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    c = cache.OrderCache(tmp_path / "orders.json")
    c.merge([Order("aliexpress", "new", "", now - timedelta(days=5), "shipped"),
             Order("aliexpress", "old", "", now - timedelta(days=200), "completed")],
            {"new": [], "old": []}, synced_to=date.today())
    asked = []
    monkeypatch.setattr(cache, "OrderCache", lambda: c)
    monkeypatch.setattr(pipeline, "_aliexpress_orders", lambda ids: asked.append(sorted(ids)) or ([], None, set()))
    pipeline.collect_orders()
    assert asked == [["new"]]


# I9: image media types come from the bytes; non-images are dropped.
def test_media_type_sniffed():
    assert haiku.media_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert haiku.media_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert haiku.media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert haiku.media_type(b"<html>") is None


def test_ask_json_skips_non_images(monkeypatch):
    import types
    sent = []

    class C:
        messages = types.SimpleNamespace(create=lambda **kw: sent.append(kw) or types.SimpleNamespace(
            stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text="{}")]))
    monkeypatch.setattr(haiku, "_client", lambda: C())
    haiku.ask_json("q", [b"\x89PNG\r\n\x1a\nx", b"<html>"], {"type": "object"})
    blocks = sent[0]["messages"][0]["content"]
    assert [b["source"]["media_type"] for b in blocks if b["type"] == "image"] == ["image/png"]


# I10: osascript failures are failures, and "no tab" is said out loud.
def test_osascript_failure_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not authorised"))
    with pytest.raises(RuntimeError, match="not authorised"):
        delivery.resume_in_new_tab(Path("/w"), "sid", "hi")


def test_pick_list_failure_is_an_error_not_a_cancel(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "denied"))
    with pytest.raises(RuntimeError):
        ui.choose([cand("a", 1)], "buzzer")


def test_no_terminal_tab_is_mentioned(tmp_path, photo_fixture):
    from parcel_finder.delivery import LiveSession
    d, calls = _deps(tmp_path)
    d = replace(d, live_session=lambda s: LiveSession(1, Path("/s"), Path("/k"), None, Path("/w"), "idle"))
    pipeline.run(photo_fixture, d)
    assert any("no Terminal tab" in n for n in calls["notify"])


# Found in the end-to-end run: a reply with no text block, and a failing session choice.
def test_ask_json_without_text_is_clear_error(monkeypatch):
    import types

    class C:
        messages = types.SimpleNamespace(create=lambda **kw: types.SimpleNamespace(
            stop_reason="max_tokens", content=[types.SimpleNamespace(type="thinking", thinking="")]))
    monkeypatch.setattr(haiku, "_client", lambda: C())
    with pytest.raises(RuntimeError, match="max_tokens"):
        haiku.ask_json("q", [], {"type": "object"})


def test_choose_failure_falls_back_to_pick_list(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    picked = []
    d = replace(d, choose=lambda *a: (_ for _ in ()).throw(RuntimeError("no text")),
                ui_choose=lambda cs, item: picked.append([c.session_id for c in cs]) or "sid-a")
    assert pipeline.run(photo_fixture, d).status == "delivered" and picked == [["sid-a"]]


# Found in the end-to-end run: AliExpress IDs known only from thumbnails (no dated email order).
def test_undated_aliexpress_ids_are_still_queried(monkeypatch, tmp_path):
    from datetime import date as _date
    now = datetime.now(timezone.utc)
    c = cache.OrderCache(tmp_path / "orders.json")
    c.merge([], {"legacy": ["https://x/t.jpg"]}, synced_to=_date.today())
    monkeypatch.setattr(cache, "OrderCache", lambda: c)
    monkeypatch.setattr(pipeline, "_aliexpress_orders", lambda ids: (
        [Order("aliexpress", "legacy", "Buzzer", now - timedelta(days=3), "shipped"),
         Order("aliexpress", "legacy", "Ancient thing", now - timedelta(days=300), "FINISH")], None, set()))
    found, _ = pipeline.collect_orders()
    assert [o.title for o in found] == ["Buzzer"] and found[0].thumbnail_urls == ["https://x/t.jpg"]


def test_medium_order_match_does_not_rename_the_item(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    m = OrderMatch(Order("aliexpress", "1", "Type-C test PCB", datetime.now(timezone.utc), "x"), "medium", "maybe")
    d = replace(d, collect_orders=lambda: ([m.order], None), match_order=lambda *a: m)
    pipeline.run(photo_fixture, d)
    assert "identified as a buzzer." in calls["send"][0]


# Found in end-to-end testing, 22 Oct 18:52.
def test_pick_list_comes_to_the_front(monkeypatch):
    scripts = []
    monkeypatch.setattr(ui, "_osascript", lambda script, *a: scripts.append(script) or "false")
    ui.choose([cand("a", 1)], "buzzer")
    assert "activate" in scripts[0].split("choose from list")[0]


def test_low_confidence_match_does_not_drive_the_search(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    queries = []
    m = OrderMatch(Order("aliexpress", "9", "", datetime.now(timezone.utc), "x"), "low", "guess")
    d = replace(d, identify=lambda p, log: Identification("SOP-24 adapter", "high", "", "haiku", "SOP24PIN"),
                collect_orders=lambda: ([m.order], None), match_order=lambda *a: m,
                search=lambda q, a: queries.append(q) or [Candidate("sid-a", "ddr-banging", "/p", datetime.now(timezone.utc), 5, "s", 5.0)])
    pipeline.run(photo_fixture, d)
    assert queries[0].startswith("SOP-24 adapter") and "SOP24PIN" in queries[0]


def test_untitled_order_never_becomes_the_query(tmp_path, photo_fixture):
    d, calls = _deps(tmp_path)
    queries = []
    m = OrderMatch(Order("aliexpress", "9", "", datetime.now(timezone.utc), "x"), "high", "thumbnail")
    d = replace(d, collect_orders=lambda: ([m.order], None), match_order=lambda *a: m,
                search=lambda q, a: queries.append(q) or [])
    pipeline.run(photo_fixture, d)
    assert queries[0] == "buzzer"


def test_api_answered_orders_do_not_fall_back(monkeypatch, tmp_path):
    from datetime import date as _date
    now = datetime.now(timezone.utc)
    c = cache.OrderCache(tmp_path / "orders.json")
    c.merge([Order("aliexpress", "cancelled-one", "", now - timedelta(days=3), "delivered"),
             Order("aliexpress", "unreachable", "", now - timedelta(days=3), "shipped")],
            {"cancelled-one": [], "unreachable": []}, synced_to=_date.today())
    monkeypatch.setattr(cache, "OrderCache", lambda: c)
    # The API answered for cancelled-one (no live lines) but failed for unreachable.
    monkeypatch.setattr(pipeline, "_aliexpress_orders", lambda ids: ([], "unreachable: network down", {"unreachable"}))
    found, _ = pipeline.collect_orders()
    assert [o.order_id for o in found] == ["unreachable"]
