import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from parcel_finder import phone
from parcel_finder.runlog import RunLog
from parcel_finder.sessions import Candidate

TOKEN = "t0ken"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def cand(sid, title):
    return Candidate(sid, title, "/p", datetime(2026, 10, 15, tzinfo=timezone.utc), 7, "s", 7.0)


@pytest.fixture
def lan(tmp_path):
    jobs = []
    runs = phone.Runs()
    srv = phone.make_lan_server(jobs.append, runs, TOKEN, port=0, photo_root=tmp_path, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", jobs, runs
    srv.shutdown()
    srv.server_close()


def call(url, path, body=None, token=TOKEN, method=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    req = urllib.request.Request(url + path, data, headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def upload(url, **extra):
    return call(url, "/runs", {"photo": base64.b64encode(JPEG).decode(), **extra})


def test_requests_without_the_paired_token_are_refused(lan):
    url, jobs, _ = lan
    assert upload(url)[0] == 202
    assert call(url, "/runs", {"photo": ""}, token=None)[0] == 401
    assert call(url, "/runs", {"photo": ""}, token="wrong")[0] == 401
    assert call(url, "/runs/x", token="wrong")[0] == 401
    assert len(jobs) == 1


def test_upload_saves_the_photo_and_queues_a_run_with_the_phones_ocr(lan, tmp_path):
    url, jobs, runs = lan
    status, body = upload(url, ocr="SINT-100 FRONT jaw")
    assert status == 202 and body["run_id"]
    job = jobs[0]
    assert job.photo.read_bytes() == JPEG and job.photo.is_relative_to(tmp_path)
    assert job.ocr_text == "SINT-100 FRONT jaw" and job.run_id == body["run_id"]
    assert runs.get(body["run_id"]).as_json()["state"] == "queued"


def test_upload_rejects_what_is_not_a_jpeg(lan):
    url, jobs, _ = lan
    assert call(url, "/runs", {"photo": base64.b64encode(b"<html>").decode()})[0] == 400
    assert call(url, "/runs", {"nope": 1})[0] == 400
    assert not jobs


def test_status_follows_the_run_log(lan, tmp_path):
    url, jobs, runs = lan
    _, body = upload(url)
    rid = body["run_id"]
    log = RunLog(rid, tmp_path, listener=runs.get(rid).on_event)
    log.event("identify", name="Core2350B board", confidence="medium")
    s = call(url, f"/runs/{rid}")[1]
    assert (s["state"], s["step"], s["item"]) == ("running", "Checking your orders", "Core2350B board")
    log.event("search", candidates=[["ddr-banging", "sid-a", 7.0]])
    log.event("choice", chosen="sid-a", clear=True)
    log.finish("delivered", session="sid-a")
    s = call(url, f"/runs/{rid}")[1]
    assert s["state"] == "done" and s["outcome"] == "delivered" and s["session"] == "ddr-banging"


def test_unknown_run_is_404(lan):
    url, _, _ = lan
    assert call(url, "/runs/nope")[0] == 404


def test_pick_list_waits_for_the_phone(lan):
    url, jobs, runs = lan
    _, body = upload(url)
    rid = body["run_id"]
    runs.get(rid).on_event("identify", {"name": "Core2350B board"})
    got = []
    t = threading.Thread(target=lambda: got.append(runs.get(rid).choose([cand("sid-a", "ddr-banging"), cand("sid-b", "tinycrypt")], "board")))
    t.start()
    for _ in range(50):
        s = call(url, f"/runs/{rid}")[1]
        if s["state"] == "waiting":
            break
        threading.Event().wait(0.02)
    assert [c["session_id"] for c in s["choices"]] == ["sid-a", "sid-b"] and s["choices"][0]["title"] == "ddr-banging"
    assert s["step"] == "Which conversation was the Core2350B board bought in?"
    assert call(url, f"/runs/{rid}/choice", {"session_id": "sid-zzz"})[0] == 400   # not one of the choices
    assert call(url, f"/runs/{rid}/choice", {"session_id": "sid-b"})[0] == 200
    t.join(5)
    assert got == ["sid-b"]


def test_pick_list_cancel_and_timeout(lan):
    url, jobs, runs = lan
    _, body = upload(url)
    r = runs.get(body["run_id"])
    threading.Timer(0.1, lambda: call(url, f"/runs/{body['run_id']}/choice", {"session_id": None})).start()
    assert r.choose([cand("sid-a", "x")], "board") is None
    assert r.choose([cand("sid-a", "x")], "board", timeout=0.05) is None


def test_finished_runs_are_forgotten_after_a_while():
    runs = phone.Runs(keep=2)
    ids = [runs.new().run_id for _ in range(4)]
    assert runs.get(ids[0]) is None and runs.get(ids[-1]) is not None


def test_a_phone_job_runs_with_its_ocr_its_status_and_the_phones_pick_list(tmp_path, monkeypatch):
    from parcel_finder import pipeline
    runs = phone.Runs()
    status = runs.new()
    seen = {}

    def fake_run(photo, deps, run_id, listener):
        seen.update(photo=photo, run_id=run_id, listener=listener, choose=deps.ui_choose)
        deps.identify(photo, None)
        return pipeline.Outcome("delivered", run_id, "sid-a", "")
    monkeypatch.setattr(phone, "_identify", lambda photo, log, ocr_text: seen.setdefault("ocr", ocr_text))
    job = phone.PhoneJob(status.run_id, tmp_path / "p.jpg", "SINT-100")
    phone.run_job(job, runs, run=fake_run)
    assert seen["run_id"] == status.run_id and seen["ocr"] == "SINT-100"
    assert seen["listener"] == status.on_event and seen["choose"] == status.choose


def test_pair_shows_a_png_and_deletes_it(monkeypatch):
    import builtins
    import os
    from parcel_finder import cli, config
    stored, pngs = {}, []
    monkeypatch.setattr(config, "store_secret", lambda svc, acct, v: stored.update({acct: v}))
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda cmd, **kw: pngs.append(cmd[cmd.index("-o") + 1]) if cmd[0] == "qrencode" else None)
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    assert cli.pair(None) == 0
    assert len(stored["phone-token"]) >= 24 and pngs and not os.path.exists(pngs[0])
