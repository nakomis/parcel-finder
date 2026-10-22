"""Loopback HTTP service: POST /photo queues a run; one worker runs them in order."""
import json
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import config

# Extra GET routes (e.g. the AliExpress OAuth callback): path -> fn(query) -> (status, text)
_ROUTES: dict[str, Callable[[dict[str, list[str]]], tuple[int, str]]] = {}


def register_route(path: str, fn: Callable[[dict[str, list[str]]], tuple[int, str]]) -> None:
    _ROUTES[path] = fn


def make_server(handler: Callable[[Path], None], host: str = config.HOST, port: int = config.PORT,
                allowed_root: Path = config.SCREENEYE_DIR) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("parcel-finder only listens on 127.0.0.1")
    jobs: "queue.Queue[Path]" = queue.Queue()

    def worker():
        while True:
            photo = jobs.get()
            try:
                handler(photo)
            except Exception as e:  # one bad run mustn't kill the worker
                print(f"run failed for {photo}: {e!r}", flush=True)
            finally:
                jobs.task_done()

    threading.Thread(target=worker, daemon=True, name="parcel-finder-worker").start()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body, content_type="application/json"):
            data = (json.dumps(body) if content_type == "application/json" else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/health":
                return self._reply(200, {"ok": True, "queue": jobs.qsize()})
            if url.path in _ROUTES:
                status, text = _ROUTES[url.path](urllib.parse.parse_qs(url.query))
                return self._reply(status, text, "text/plain; charset=utf-8")
            self._reply(404, {"error": "not found"})

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/photo":
                return self._reply(404, {"error": "not found"})
            # A web page can POST text/plain to localhost without a CORS preflight; JSON forces one,
            # which this server never answers. The Host check stops DNS rebinding.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._reply(415, {"error": "expected application/json"})
            if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
                return self._reply(403, {"error": "forbidden host"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                photo = Path(body["path"]).expanduser()
            except (ValueError, KeyError, TypeError):
                return self._reply(400, {"error": 'expected JSON {"path": "..."}'})
            if not photo.is_file():
                return self._reply(400, {"error": f"no such file: {photo}"})
            if not photo.resolve().is_relative_to(allowed_root.resolve()):
                return self._reply(403, {"error": f"photos must be under {allowed_root}"})
            jobs.put(photo)
            self._reply(202, {"queued": True, "path": str(photo)})

        def log_message(self, *args):  # the run log is the record, not stderr
            pass

    return ThreadingHTTPServer((host, port), Handler)
