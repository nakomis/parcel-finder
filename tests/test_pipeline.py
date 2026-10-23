import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from parcel_finder import pipeline
from parcel_finder.delivery import LiveSession
from parcel_finder.identify import Identification
from parcel_finder.orders.model import Order, OrderMatch
from parcel_finder.pipeline import Deps
from parcel_finder.sessions import Candidate, SessionChoice

T = datetime(2026, 10, 19, 21, 55, tzinfo=timezone.utc)
IDENT = Identification("buzzer module", "medium", "label", "haiku", None)
ORDER = Order("aliexpress", "1", "Estardyn Buzzer Module", T, "shipped")
CAND = Candidate("sid-a", "ddr-banging", "/p", T, 12, "s", 12.0)
CAND_B = Candidate("sid-b", "other", "/p", T, 2, "s", 2.0)


@pytest.fixture
def deps(tmp_path):
    calls = {"notify": [], "send": [], "resume": [], "raise": [], "choose": []}
    d = Deps(
        identify=lambda photo, log: IDENT,
        collect_orders=lambda: ([ORDER], None),
        match_order=lambda photo, ident, orders: OrderMatch(ORDER, "high", "same board"),
        search=lambda query, around: [CAND, CAND_B],
        choose=lambda ident, match, cands: SessionChoice(CAND, True, cands, "clear"),
        ui_choose=lambda cands, item: calls["choose"].append(item) or "sid-b",
        local_transcript=lambda sid: Path(f"/t/{sid}.jsonl"),
        copy_photo=lambda photo, slug, now: (Path("/share/p.jpg"), True),
        live_session=lambda sid: LiveSession(1, Path("/s"), Path("/k"), "ttys001", Path("/w"), "idle"),
        send=lambda live, text: calls["send"].append(text),
        raise_tab=lambda tty: calls["raise"].append(tty) or True,
        resume=lambda cwd, sid, text: calls["resume"].append((sid, text)),
        notify=lambda title, text: calls["notify"].append(text),
        log_dir=tmp_path,
    )
    d.calls = calls
    return d


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]


def test_clear_match_delivers_without_asking(deps, photo_fixture):
    out = pipeline.run(photo_fixture, deps)
    assert out.status == "delivered" and out.session_id == "sid-a"
    assert deps.calls["choose"] == [] and deps.calls["raise"] == ["ttys001"]
    assert deps.calls["send"][0].startswith("This just arrived! It's been identified as a Estardyn Buzzer Module.")
    assert deps.calls["send"][0].endswith("Photo: /share/p.jpg")
    assert any("ddr-banging" in n for n in deps.calls["notify"])


def test_unclear_match_asks_and_respects_cancel(deps, photo_fixture):
    d = replace(deps, choose=lambda i, m, c: SessionChoice(CAND, False, c, "close"), ui_choose=lambda c, item: None)
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d)
    assert out.status == "cancelled" and deps.calls["send"] == [] and deps.calls["resume"] == []


def test_unclear_match_uses_picked_session(deps, photo_fixture):
    d = replace(deps, choose=lambda i, m, c: SessionChoice(CAND, False, c, "close"))
    d.calls = deps.calls
    assert pipeline.run(photo_fixture, d).session_id == "sid-b" and deps.calls["choose"] == ["Estardyn Buzzer Module"]


def test_closed_session_is_resumed(deps, photo_fixture):
    d = replace(deps, live_session=lambda sid: None)
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d)
    assert out.status == "resumed" and deps.calls["resume"][0][0] == "sid-a" and deps.calls["send"] == []


def test_session_elsewhere_stops(deps, photo_fixture):
    d = replace(deps, local_transcript=lambda sid: None)
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d)
    assert out.status == "elsewhere" and deps.calls["send"] == [] and deps.calls["resume"] == []
    assert any("not on this Mac" in n for n in deps.calls["notify"])


def test_pipeline_continues_without_aliexpress(deps, photo_fixture):   # Review Focus 3
    d = replace(deps, collect_orders=lambda: ([ORDER], "refresh token missing or lapsed"))
    d.calls = deps.calls
    assert pipeline.run(photo_fixture, d).status == "delivered"
    assert any("parcel-finder authorise" in n for n in deps.calls["notify"])


def test_nothing_found_sends_nothing(deps, photo_fixture, tmp_path):   # Review Focus 5
    d = replace(deps, match_order=lambda *a: None, search=lambda q, a: [],
                choose=lambda i, m, c: SessionChoice(None, False, [], "none"))
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d)
    assert out.status == "not-found" and deps.calls["send"] == [] and deps.calls["resume"] == []
    assert any(out.run_id in n for n in deps.calls["notify"])


def test_no_order_match_searches_by_identified_item(deps, photo_fixture):
    queries = []
    d = replace(deps, match_order=lambda *a: None, search=lambda q, a: queries.append((q, a)) or [CAND])
    d.calls = deps.calls
    pipeline.run(photo_fixture, d)
    assert queries == [("buzzer module", None)]


def test_every_stage_logged(deps, photo_fixture, tmp_path):
    out = pipeline.run(photo_fixture, deps)
    stages = [e["stage"] for e in events(tmp_path)]
    assert {"start", "orders", "match", "search", "choice", "deliver", "finish"} <= set(stages)
    assert {e["run_id"] for e in events(tmp_path)} == {out.run_id}


def test_dry_run_delivers_nothing(deps, photo_fixture):
    out = pipeline.run(photo_fixture, deps, dry_run=True)
    assert out.status == "dry-run" and deps.calls["send"] == [] and deps.calls["raise"] == []


def test_stage_exception_becomes_error_outcome(deps, photo_fixture):
    d = replace(deps, identify=lambda p, log: (_ for _ in ()).throw(RuntimeError("ollama exploded")))
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d)
    assert out.status == "error" and "ollama exploded" in out.detail
    assert any(out.run_id in n for n in deps.calls["notify"])


def test_dry_run_never_asks(deps, photo_fixture):
    d = replace(deps, choose=lambda i, m, c: SessionChoice(CAND, False, c, "close"))
    d.calls = deps.calls
    out = pipeline.run(photo_fixture, d, dry_run=True)
    assert out.status == "dry-run" and deps.calls["choose"] == [] and "would ask" in out.detail


def test_search_uses_short_name_and_ocr_terms(deps, photo_fixture):
    queries = []
    ident = Identification("SOP-24 adapter", "high", "", "haiku", "SOP24PIN 1.27MM", "a very long description")
    d = replace(deps, identify=lambda p, log: ident, match_order=lambda *a: None,
                search=lambda q, a: queries.append(q) or [CAND])
    d.calls = deps.calls
    pipeline.run(photo_fixture, d)
    assert queries[0].startswith("SOP-24 adapter") and "SOP24PIN" in queries[0] and "long" not in queries[0]
