from datetime import datetime, timezone

from parcel_finder import sessions
from parcel_finder.identify import Identification
from parcel_finder.orders.model import Order, OrderMatch
from parcel_finder.sessions import Candidate, choose, local_transcript, rank

ORDER_AT = datetime(2026, 10, 19, 21, 55, tzinfo=timezone.utc)
IDENT = Identification("buzzer module", "medium", "label", "haiku", None)
MATCH = OrderMatch(Order("aliexpress", "1", "Buzzer Module", ORDER_AT, "shipped"), "high", "looks right")


def hit(sid, when, name="s"):
    return {"session_id": sid, "name": name, "ai_title": None, "project_path": "/p", "created_at_utc": when,
            "context": [{"role": "assistant", "content": "added the buzzer to the basket"}]}


def test_time_weighting_prefers_sessions_before_order():
    results = [hit("old", "2026-09-01T10:00:00+00:00"), hit("old", "2026-09-01T11:00:00+00:00"),
               hit("near", "2026-10-19T21:30:00+00:00")]
    ranked = rank(results, around=ORDER_AT)
    assert ranked[0].session_id == "near"


def test_rank_without_order_uses_hits():
    results = [hit("a", "2026-09-01T10:00:00+00:00"), hit("b", "2026-10-19T21:30:00+00:00"),
               hit("b", "2026-10-19T21:31:00+00:00")]
    assert [c.session_id for c in rank(results, around=None)] == ["b", "a"]


def cand(sid, score):
    return Candidate(sid, sid, "/p", ORDER_AT, 5, "snippet", score)


def test_choice_not_clear_without_order_match(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "a", "confidence": "high", "reason": "r"})
    c = choose(IDENT, None, [cand("a", 5), cand("b", 1)])
    assert c.chosen.session_id == "a" and not c.clear


def test_choice_not_clear_when_runner_up_close(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "a", "confidence": "high", "reason": "r"})
    assert not choose(IDENT, MATCH, [cand("a", 5), cand("b", 4)]).clear


def test_choice_clear_with_match_and_clear_winner(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "a", "confidence": "high", "reason": "r"})
    assert choose(IDENT, MATCH, [cand("a", 5), cand("b", 1)]).clear


def test_choice_none_when_haiku_picks_nothing(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: {"session_id": "", "confidence": "low", "reason": "r"})
    c = choose(IDENT, MATCH, [cand("a", 5)])
    assert c.chosen is None and not c.clear


def test_no_candidates_skips_haiku(monkeypatch):
    monkeypatch.setattr(sessions.haiku, "ask_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert choose(IDENT, MATCH, []).chosen is None


def test_local_transcript_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions.config, "PROJECTS_DIR", tmp_path)
    assert local_transcript("0aedf57a-0000") is None
    (tmp_path / "-Users-x").mkdir()
    (tmp_path / "-Users-x" / "0aedf57a-0000.jsonl").write_text("{}")
    assert local_transcript("0aedf57a-0000") == tmp_path / "-Users-x" / "0aedf57a-0000.jsonl"
