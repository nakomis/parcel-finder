from datetime import datetime

from parcel_finder import ui
from parcel_finder.sessions import Candidate

CANDS = [Candidate("sid-a", "ddr-banging", "/p", datetime(2026, 10, 12), 12, "s", 12.0),
         Candidate("sid-b", "esp32-embedder", "/p", datetime(2026, 10, 13), 2, "s", 2.0)]


def test_choose_maps_label_back_to_session(monkeypatch):
    seen = []
    monkeypatch.setattr(ui, "_osascript", lambda script, *args: seen.append(args) or args[2])
    assert ui.choose(CANDS, "USB tester") == "sid-b"
    assert seen[0][0] == "Which conversation was the USB tester bought in?"


def test_choose_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(ui, "_osascript", lambda script, *args: "false")
    assert ui.choose(CANDS, "USB tester") is None
