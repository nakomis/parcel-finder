"""Runs started from the iPhone (ScreenEye's Open in Claude): a LAN listener beside the loopback one.

The phone uploads the photo and the text Vision read from it, then polls the run's status; when the
choice of session isn't clear, the run waits for the phone to choose. Every request carries the token
paired with `parcel-finder pair` (keychain: parcel-finder/phone-token). Plain HTTP on the home LAN."""
import base64
import binascii
import hmac
import json
import re
import secrets
import subprocess
import threading
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import config

MAX_BODY = 40 * 1024 * 1024  # a 4K JPEG, base64, with room to spare
CHOOSE_TIMEOUT = 600
# What the run is doing after each logged stage (the phone shows it as the current step).
STEP_AFTER = {"start": "Identifying the item", "ocr": "Identifying the item", "rule": "Checking your orders",
              "identify": "Checking your orders", "orders": "Matching an order", "match": "Finding the conversation",
              "search": "Choosing the conversation", "choice": "Delivering", "deliver": "Delivering"}


@dataclass
class PhoneJob:
    run_id: str
    photo: Path
    ocr_text: str | None


@dataclass
class RunStatus:
    run_id: str
    state: str = "queued"  # queued | running | waiting | done
    step: str = "Waiting for an earlier run"
    item: str | None = None
    session: str | None = None
    outcome: str | None = None
    detail: str = ""
    choices: list[dict] = field(default_factory=list)
    _titles: dict[str, str] = field(default_factory=dict)
    _picked: list = field(default_factory=list)
    _answered: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def on_event(self, stage: str, data: dict) -> None:
        """RunLog listener: keeps the status the phone polls in step with the run."""
        with self._lock:
            if stage == "finish":
                self.state, self.outcome, self.detail = "done", data.get("outcome"), data.get("detail", "")
                self.session = self._titles.get(data.get("session") or "", self.session)
                return
            self.state, self.step = "running", STEP_AFTER.get(stage, self.step)
            if stage in ("rule", "identify"):
                self.item = data.get("verdict") or data.get("name") or self.item
            elif stage == "search":
                self._titles = {sid: title for title, sid, _ in data.get("candidates", [])}
            elif stage in ("choice", "deliver"):
                sid = data.get("chosen") or data.get("session")
                self.session = self._titles.get(sid or "", self.session)

    def choose(self, candidates: list, item: str, timeout: float = CHOOSE_TIMEOUT) -> str | None:
        """The pick-list, on the phone: wait for its answer (None: cancelled or no answer in time)."""
        with self._lock:
            self.choices = [{"session_id": c.session_id, "title": c.title or c.session_id[:8],
                             "when": f"{c.when:%d %b %Y}", "hits": c.hits} for c in candidates]
            # The identified short name, not an order's long shop title.
            self.state, self.step = "waiting", f"Which conversation was the {self.item or item} bought in?"
            self._answered.clear()
        answered = self._answered.wait(timeout)
        with self._lock:
            self.choices, self.state = [], "running"
            picked = self._picked[-1] if answered and self._picked else None
        if picked:
            self.session = next((c.title for c in candidates if c.session_id == picked), None)
        return picked

    def answer(self, session_id: str | None) -> bool:
        with self._lock:
            if self.state != "waiting" or (session_id is not None and
                                           session_id not in {c["session_id"] for c in self.choices}):
                return False
            self._picked.append(session_id)
        self._answered.set()
        return True

    def as_json(self) -> dict:
        with self._lock:
            return {"run_id": self.run_id, "state": self.state, "step": self.step, "item": self.item,
                    "session": self.session, "outcome": self.outcome, "detail": self.detail,
                    "choices": list(self.choices)}


class Runs:
    """Statuses of recent phone runs, newest last; the oldest are forgotten."""

    def __init__(self, keep: int = 50):
        self.keep, self._runs, self._lock = keep, OrderedDict(), threading.Lock()

    def new(self) -> RunStatus:
        with self._lock:
            r = RunStatus(f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}")
            self._runs[r.run_id] = r
            while len(self._runs) > self.keep:
                self._runs.popitem(last=False)
            return r

    def get(self, run_id: str) -> RunStatus | None:
        with self._lock:
            return self._runs.get(run_id)


def _identify(photo: Path, log, ocr_text: str | None):
    from .identify import identify
    return identify(photo, log, ocr_text=ocr_text)


def run_job(job: PhoneJob, runs: Runs, run: Callable | None = None, deps=None):
    """A phone run: the phone's OCR, its status for polling, and its pick-list."""
    from dataclasses import replace
    from . import pipeline
    status = runs.get(job.run_id) or runs.new()
    deps = replace(deps or pipeline.Deps.real(), ui_choose=status.choose,
                   identify=lambda photo, log: _identify(photo, log, job.ocr_text))
    return (run or pipeline.run)(job.photo, deps=deps, run_id=job.run_id, listener=status.on_event)


def make_lan_server(enqueue: Callable[[PhoneJob], None], runs: Runs, token: str, port: int = config.LAN_PORT,
                    photo_root: Path = config.SCREENEYE_DIR, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    expected = f"Bearer {token}".encode()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            if hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected):
                return True
            self._reply(401, {"error": "not paired: run `parcel-finder pair` on the Mac"})
            return False

        def _body(self) -> dict | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._reply(413, {"error": "too large"})
                return None
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                return body if isinstance(body, dict) else {}
            except ValueError:
                return {}

        def _run(self) -> tuple[RunStatus | None, str]:
            m = re.fullmatch(r"/runs/([\w-]+)(/choice)?", urllib.parse.urlparse(self.path).path)
            return (runs.get(m.group(1)) if m else None), (m.group(2) or "" if m else "")

        def do_GET(self):
            if not self._authorised():
                return
            run, rest = self._run()
            if run is None or rest:
                return self._reply(404, {"error": "no such run"})
            self._reply(200, run.as_json())

        def do_POST(self):
            if not self._authorised():
                return
            path = urllib.parse.urlparse(self.path).path
            body = self._body()
            if body is None:
                return
            if path == "/runs":
                return self._new_run(body)
            run, rest = self._run()
            if run is None or rest != "/choice":
                return self._reply(404, {"error": "no such run"})
            if "session_id" not in body or not run.answer(body["session_id"]):
                return self._reply(400, {"error": "not waiting for a choice, or not one of the choices"})
            self._reply(200, run.as_json())

        def _new_run(self, body: dict):
            try:
                jpeg = base64.b64decode(body["photo"], validate=True)
            except (KeyError, TypeError, binascii.Error):
                return self._reply(400, {"error": 'expected JSON {"photo": "<base64 JPEG>", "ocr": "..."}'})
            if not jpeg.startswith(b"\xff\xd8\xff"):
                return self._reply(400, {"error": "photo is not a JPEG"})
            ocr = body.get("ocr")
            run = runs.new()
            dest = photo_root / f"{datetime.now():%Y-%m-%d}" / f"{datetime.now():%H%M%S}-phone.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(jpeg)
            enqueue(PhoneJob(run.run_id, dest, ocr if isinstance(ocr, str) else None))
            self._reply(202, run.as_json())

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def advertise(port: int = config.LAN_PORT) -> subprocess.Popen:
    """Bonjour, so the phone finds the Mac: lives as long as the service (launchd ends the group)."""
    return subprocess.Popen(["dns-sd", "-R", "parcel-finder", "_parcel-finder._tcp", "local", str(port), "path=/runs"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
