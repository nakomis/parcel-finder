import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from parcel_finder.service import make_server


@pytest.fixture
def serve(tmp_path):
    root = tmp_path
    servers = []

    def _start(handler):
        srv = make_server(handler, port=0, allowed_root=root)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield _start
    for s in servers:
        s.shutdown()
        s.server_close()


def post(url, body):
    req = urllib.request.Request(url + "/photo", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5)


def test_post_path_enqueues(serve, tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpg")
    seen = []
    done = threading.Event()
    url = serve(lambda p: (seen.append(p), done.set()))
    r = post(url, {"path": str(photo)})
    assert r.status == 202 and json.load(r) == {"queued": True, "path": str(photo)}
    assert done.wait(5) and seen == [photo]


def test_missing_file_is_400(serve, tmp_path):
    url = serve(lambda p: None)
    with pytest.raises(urllib.error.HTTPError) as e:
        post(url, {"path": str(tmp_path / "nope.jpg")})
    assert e.value.code == 400


def test_runs_are_serialised(serve, tmp_path):
    spans, done = [], threading.Event()

    def handler(p):
        start = time.monotonic()
        time.sleep(0.2)
        spans.append((start, time.monotonic()))
        if len(spans) == 2:
            done.set()

    url = serve(handler)
    for name in ("a.jpg", "b.jpg"):
        (tmp_path / name).write_bytes(b"jpg")
        post(url, {"path": str(tmp_path / name)})
    assert done.wait(5)
    assert spans[1][0] >= spans[0][1]


def test_rejects_non_loopback_bind():
    with pytest.raises(ValueError):
        make_server(lambda p: None, host="0.0.0.0", port=0)


def test_health(serve):
    url = serve(lambda p: None)
    assert json.load(urllib.request.urlopen(url + "/health", timeout=5)) == {"ok": True, "queue": 0}


def test_registered_route(serve):
    from parcel_finder import service
    service.register_route("/hello", lambda query: (200, "hi " + query.get("name", [""])[0]))
    url = serve(lambda p: None)
    assert urllib.request.urlopen(url + "/hello?name=Boots", timeout=5).read() == b"hi Boots"
