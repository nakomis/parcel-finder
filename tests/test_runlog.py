import json
from pathlib import Path

from parcel_finder.runlog import RunLog


def test_event_redacts_secret_like_keys(tmp_path):
    log = RunLog.start(Path("/p.jpg"), log_dir=tmp_path)
    log.event("orders", access_token="abc", nested={"Code": "x", "title": "Buzzer"})
    line = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert line["access_token"] == "<redacted>"
    assert line["nested"] == {"Code": "<redacted>", "title": "Buzzer"}
    assert line["run_id"] == log.run_id and line["stage"] == "orders"


def test_finish_writes_human_line(tmp_path):
    log = RunLog.start(Path("/p.jpg"), log_dir=tmp_path)
    log.finish("delivered", session="ddr-banging")
    assert f"{log.run_id} delivered" in (tmp_path / "finder.log").read_text()


def test_start_records_photo(tmp_path):
    log = RunLog.start(Path("/p.jpg"), log_dir=tmp_path)
    first = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[0])
    assert first == {**first, "run_id": log.run_id, "stage": "start", "photo": "/p.jpg"}
